# Copyright (c) 2013-2014 Rackspace, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Server-side (i.e. worker side) classes and logic.
"""
import time

from oslo.config import cfg

from barbican.common import nova
from barbican.common import utils
from barbican.openstack.common import periodic_task
from barbican.openstack.common import service
from barbican.queue import client
from barbican.tasks import resources
from barbican import queue


LOG = utils.getLogger(__name__)

CONF = cfg.CONF

RETRY_MANAGER = None


def get_max_retries():
    max_retries = CONF.queue.task_max_retries if CONF.queue.enable else 0
    return max_retries


def get_retry_seconds():
    retry_seconds = CONF.queue.task_retry_seconds if CONF.queue.enable else 0
    return retry_seconds


def get_retry_manager():
    global RETRY_MANAGER
    if not RETRY_MANAGER:
        RETRY_MANAGER = TaskRetryManager()
    return RETRY_MANAGER


def invocable_task(fn):
    """Decorator to support task invocations and retries."""
    def retry_decorator(inst, context, *args, **kwargs):

        # Get task instance.
        task = fn(inst, context, *args, **kwargs)

        # Let the task do its work
        max_retries = get_max_retries()
        retry_seconds = get_retry_seconds()
        retry_manager = get_retry_manager()
        LOG.debug("Beginning task '{0}' after "
                  "retry #{1}".format(task.get_name(),
                                      kwargs.get('num_retries_so_far', 0)))
        LOG.debug("   Args: '{0}'".format(args))
        LOG.debug("   Kwargs: '{0}'".format(kwargs))
        try:
            task.process(max_retries, *args, **kwargs)
        except Exception:
            LOG.exception('>>>>> Task exception '
                          'seen for task: {0}'.format(task.get_name()))
            retry_manager.retry(fn.__name__, max_retries, retry_seconds,
                                *args, **kwargs)
        else:
            # Successful completion of task, remove from manager.
            retry_manager.remove(fn.__name__, *args, **kwargs)

    return retry_decorator


class Tasks(object):
    """Tasks that can be invoked asynchronously in Barbican.

    Only place task methods and implementations on this class, as they can be
    called directly from the client side for non-asynchronous standalone
    single-node operation.

    The TaskServer class below extends this class to implement a worker-side
    server utilizing Oslo messaging's RPC server. This RPC server can invoke
    methods on itself, which include the methods in this class.
    """

    def __init__(self):
        super(Tasks, self).__init__()
        self._nova = nova.NovaClient()

    @invocable_task
    def process_order(self, context, order_id, keystone_id,
                      num_retries_so_far=0):
        return resources.BeginOrder()

    @invocable_task
    def process_verification(self, context, verification_id,
                             keystone_id, num_retries_so_far=0):
        return resources.PerformVerification(self._nova)


class TaskServer(Tasks, service.Service, periodic_task.PeriodicTasks):
    """Server to process asynchronous tasking from Barbican API nodes.

    This server is an Oslo service that exposes task methods that can
    be invoked from the Barbican API nodes. It delegates to an Oslo
    RPC messaging server to invoke methods asynchronously on this class.
    Since this class also extends the Tasks class above, its task-based
    methods are hence available to the RPC messaging server.
    """
    def __init__(self):
        super(TaskServer, self).__init__()

        # This property must be defined for the 'endpoints' specified below,
        #   as the oslo.messaging RPC server will ask for it.
        self.target = queue.get_target()

        # Create an oslo RPC server, that calls back on to this class
        #   instance to invoke tasks, such as 'process_order()' on the
        #   extended Tasks class above.
        self._server = queue.get_server(target=self.target,
                                        endpoints=[self])

        # Configure ourselves as a client to the queue, so we can
        #   retry RPC messages, for example
        self.queue = client.TaskClient(alternate_client=
                                       DirectTaskInvokerClient())

        # Start the task retry periodic scheduler process up.
        self.tg\
            .add_dynamic_timer(self._check_retry_tasks,
                               initial_delay=
                               CONF.queue.task_retry_tg_initial_delay,
                               periodic_interval_max=
                               CONF.queue.task_retry_tg_periodic_interval_max)

    def start(self):
        self._server.start()
        super(TaskServer, self).start()

    def stop(self):
        super(TaskServer, self).stop()
        self._server.stop()

    @periodic_task.periodic_task
    def _check_retry_tasks(self):
        """Periodically check to see if tasks need to be scheduled."""
        LOG.debug("Processing scheduled retry tasks")
        return get_retry_manager()\
            .schedule_retries(CONF.queue.task_retry_scheduler_cycle,
                              self)  # self.queue)


class TaskRetryManager(object):
    """Manages failed tasks that need to be retried."""
    def __init__(self):
        super(TaskRetryManager, self).__init__()

        self.num_retries_so_far = dict()
        self.countdown_seconds = dict()
        self.start_timestamps = dict()

        self._is_busy = False

    def retry(self, retry_method, max_retries, retry_seconds,
              *args, **kwargs):

        num_retries_so_far = kwargs.get('num_retries_so_far', 0)

        retryKey = self._generate_key_for(retry_method,
                                          *args, **kwargs)

        retries = 1 + num_retries_so_far
        if retries <= max_retries:
            LOG.debug("Saving task state for retry later "
                      "via call to '{0}'".format(retry_method))
            self.num_retries_so_far[retryKey] = retries
            self.countdown_seconds[retryKey] = retry_seconds
            self.start_timestamps[retryKey] = time.time()
        else:
            LOG.debug("Discontinuing retries for task call to "
                      "'{0}'".format(retry_method))
            self._remove_key(retryKey)

        LOG.debug("   Args: '{0}'".format(args))
        LOG.debug("   Kwargs: '{0}'".format(kwargs))

    def remove(self, retry_method, *args, **kwargs):
        retryKey = self._generate_key_for(retry_method,
                                          *args, **kwargs)
        self._remove_key(retryKey)

    def schedule_retries(self, seconds_between_retries, queue_client):
        """Invoke callback functions for tasks that are ready to retry."""
        if self._is_busy:
            return seconds_between_retries

        self._is_busy = True
        try:
            self._schedule_retries(seconds_between_retries, queue_client)
        finally:
            self._is_busy = False

        return seconds_between_retries

    def _schedule_retries(self, seconds_between_retries, queue_client):
        for retryKey, time_since_start in list(self.start_timestamps.items()):

            countdown_seconds = self.countdown_seconds.get(retryKey, 0)
            time_elapsed_sec = int(0.5 + time.time() - time_since_start)

            if time_elapsed_sec > countdown_seconds:
                self._invoke_client_method(retryKey, queue_client)

        return seconds_between_retries

    def _generate_key_for(self, retry_method, *args, **kwargs):
        local_kwargs = dict(kwargs)
        if 'num_retries_so_far' in local_kwargs:
            del local_kwargs['num_retries_so_far']
        return (retry_method,
                frozenset(args),
                frozenset(local_kwargs.items()))

    def _remove_key(self, retryKey):
        if not retryKey:
            return

        self.num_retries_so_far.pop(retryKey, None)
        self.countdown_seconds.pop(retryKey, None)
        self.start_timestamps.pop(retryKey, None)

    def _invoke_client_method(self, retryKey, queue_client):
        """Invoke queue client, to place retried task in the RPC queue."""
        retry_method_name = '???'
        try:
            retry_method_name, args_set, kwargs_set = retryKey
            args = list(args_set)
            kwargs = dict(kwargs_set)

            # Add the retries_so_far attribute, removed when key generated.
            retries_so_far = self.num_retries_so_far.get(retryKey, 0)
            kwargs['num_retries_so_far'] = retries_so_far

            # Invoke queue client to place retried RPC task on queue.
            retry_method = getattr(queue_client, retry_method_name)
            LOG.debug("Invoking method '{0}' on queue "
                      "client".format(retry_method_name))
            LOG.debug("   Args: '{0}'".format(args))
            LOG.debug("   Kwargs: '{0}'".format(kwargs))
            retry_method(None, *args, **kwargs)
            LOG.debug('   Done executing retry method:')
            LOG.debug("       Args: '{0}'".format(args))
            LOG.debug("       Kwargs: '{0}'".format(kwargs))
        except Exception:
            LOG.exception('Problem executing scheduled '
                          'retry task: {0}'.format(retry_method_name))


class DirectTaskInvokerClient(object):
    """Allows for direct invocation of queue.server Tasks from clients.

    This class supports a standalone single-node mode of operation for
    Barbican, whereby typically asynchronous requests to Barbican are
    handled synchronously.
    """

    def __init__(self):
        super(DirectTaskInvokerClient, self).__init__()

        self._tasks = Tasks()

    def cast(self, context, method_name, **kwargs):
        try:
            getattr(self._tasks, method_name)(context, **kwargs)
        except Exception:
            LOG.exception(">>>>> Task exception seen for synchronous task "
                          "invocation, so handling exception to mimic "
                          "asynchronous behavior.")

    def call(self, context, method_name, **kwargs):
        raise ValueError("No support for call() client methods.")
