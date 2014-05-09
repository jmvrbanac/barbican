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
Client-side (i.e. API side) classes and logic.
"""
from barbican.common import utils
from barbican import queue

LOG = utils.getLogger(__name__)


class TaskClient(object):
    """API-side client interface to asynchronous queuing services.

    The class delegates calls to the oslo.messaging RPC framework.
    """
    def __init__(self, alternate_client=None):
        super(TaskClient, self).__init__()

        # Establish either an asynchronous messaging/queuing client
        #   interface (via Oslo's RPC messaging) or else allow for
        #   synchronously invoking worker processes in support of a
        #   standalone single-node mode for Barbican.
        self._client = queue.get_client() or alternate_client

    def process_order(self, order_id, keystone_id, num_retries_so_far=0):
        """Process Order."""

        self._cast('process_order', order_id=order_id,
                   keystone_id=keystone_id,
                   num_retries_so_far=num_retries_so_far)

    def process_verification(self, verification_id, keystone_id,
                             num_retries_so_far=0):
        """Process Verification."""
        self._cast('process_verification', verification_id=verification_id,
                   keystone_id=keystone_id,
                   num_retries_so_far=num_retries_so_far)

    def _cast(self, name, **kwargs):
        """Asynchronous call handler. Barbican probably only needs casts.

        :param name: Method name to invoke.
        :param kwargs: Arguments for the method invocation.
        :return:
        """
        return self._client.cast({}, name, **kwargs)

    def _call(self, name, **kwargs):
        """Synchronous call handler. Barbican probably *never* uses calls."""
        return self._client.call({}, name, **kwargs)
