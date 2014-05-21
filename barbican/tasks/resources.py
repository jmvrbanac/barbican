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
Task resources for the Barbican API.
"""
import abc
from novaclient import exceptions as nex
import requests

from barbican import api
from barbican.common import exception
from barbican.common import resources as res
from barbican.common import utils
from barbican.crypto import extension_manager as em
from barbican.model import models
from barbican.model import repositories as rep
from barbican.openstack.common import gettextutils as u

LOG = utils.getLogger(__name__)


def shorten_error_status(status):
    """Shorten status."""
    return status.split(' ')[0] if status else '???'


class NovaServerNotReadyException(exception.BarbicanException):
    """Raised when a Nova server is not ready for verification."""
    def __init__(self):
        super(NovaServerNotReadyException, self).__init__(
            u._('Nova server is not ready for '
                'verification as it is still building.')
        )


class BaseTask(object):
    """Base asychronous task."""

    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def get_name(self):
        """A hook method to return a short localized name for this task.
        The returned name in the form 'u.('Verb Noun')'. For example:
            u._('Create Secret')
        """

    def process(self, retries_allowed, *args, **kwargs):
        """A template method for all asynchronous tasks.

        This method should not be overridden by sub-classes. Rather the
        abstract methods below should be overridden.

        :param retries_allowed: True if retries are allowed on failures.
        :param args: List of arguments passed in from the client.
        :param kwargs: Dict of arguments passed in from the client.
        :return: None
        """

        name = self.get_name()

        # Retrieve the target entity (such as an models.Order instance).
        try:
            entity = self.retrieve_entity(*args, **kwargs)
        except Exception as e:
            # Serious error!
            LOG.exception(u._("Could not retrieve information needed to "
                              "process task '{0}'.").format(name))
            raise e

        # Process the target entity.
        try:
            self.handle_processing(entity, *args, **kwargs)
        except Exception as e_orig:
            LOG.exception(u._("Could not perform processing for "
                              "task '{0}'.").format(name))
            LOG.debug("   ...Args: '{0}'".format(args))
            LOG.debug("   ...Kwargs: '{0}'".format(kwargs))

            # Handle failure to process entity.
            try:
                status, message = api \
                    .generate_safe_exception_message(name, e_orig)
                self.handle_error(entity, shorten_error_status(status),
                                  message, e_orig, retries_allowed,
                                  *args, **kwargs)
            except Exception:
                LOG.exception(u._("Problem handling an error for task '{0}', "
                                  "raising original "
                                  "exception.").format(name))
            raise e_orig

        # Handle successful conclusion of processing.
        try:
            self.handle_success(entity, *args, **kwargs)
        except Exception as e:
            LOG.exception(u._("Could not process after successfully executing"
                              " task '{0}'.").format(name))
            raise e

    @abc.abstractmethod
    def retrieve_entity(self, *args, **kwargs):
        """A hook method to retrieve an entity for processing.

        :param args: List of arguments passed in from the client.
        :param kwargs: Dict of arguments passed in from the client.
        :return: Entity instance to process in subsequent hook methods.
        """

    @abc.abstractmethod
    def handle_processing(self, entity, *args, **kwargs):
        """A hook method to handle processing on behalf of an entity.

        :param args: List of arguments passed in from the client.
        :param kwargs: Dict of arguments passed in from the client.
        :return: None
        """

    @abc.abstractmethod
    def handle_error(self, entity, status, message, exception,
                     task_will_be_retried, *args, **kwargs):
        """A hook method to deal with errors seen during processing.

        This method could be used to mark entity as being in error, and/or
        to record an error cause.

        :param entity: Entity retrieved from _retrieve_entity() above.
        :param status: Status code for exception.
        :param message: Reason/message for the exception.
        :param exception: Exception raised from handle_processing() above.
        :param task_will_be_retried: True if task will be retried, so
                                     this method should keep relevant record
                                     in the PENDING state. Otherwise record
                                     can be marked as ERROR.
        :param args: List of arguments passed in from the client.
        :param kwargs: Dict of arguments passed in from the client.
        :return: None
        """

    @abc.abstractmethod
    def handle_success(self, entity, *args, **kwargs):
        """A hook method to post-process after successful entity processing.

        This method could be used to mark entity as being active, or to
        add information/references to the entity.

        :param entity: Entity retrieved from _retrieve_entity() above.
        :param args: List of arguments passed in from the client.
        :param kwargs: Dict of arguments passed in from the client.
        :return: None
        """


class BeginOrder(BaseTask):
    """Handles beginning processing an Order"""

    def get_name(self):
        return u._('Create Secret')

    def __init__(self, crypto_manager=None, tenant_repo=None, order_repo=None,
                 secret_repo=None, tenant_secret_repo=None,
                 datum_repo=None, kek_repo=None):
        LOG.debug('Creating BeginOrder task processor')
        self.order_repo = order_repo or rep.OrderRepo()
        self.tenant_repo = tenant_repo or rep.TenantRepo()
        self.secret_repo = secret_repo or rep.SecretRepo()
        self.tenant_secret_repo = tenant_secret_repo or rep.TenantSecretRepo()
        self.datum_repo = datum_repo or rep.EncryptedDatumRepo()
        self.kek_repo = kek_repo or rep.KEKDatumRepo()
        self.crypto_manager = crypto_manager or em.CryptoExtensionManager()

    def retrieve_entity(self, order_id, keystone_id):
        return self.order_repo.get(entity_id=order_id,
                                   keystone_id=keystone_id)

    def handle_processing(self, order, *args, **kwargs):
        self.handle_order(order)

    def handle_error(self, order, status, message, exception,
                     task_will_be_retried, *args, **kwargs):
        order.status = models.States.PENDING if task_will_be_retried \
            else models.States.ERROR
        order.error_status_code = status
        order.error_reason = message
        self.order_repo.save(order)

    def handle_success(self, order, *args, **kwargs):
        order.status = models.States.ACTIVE
        self.order_repo.save(order)

    def handle_order(self, order):
        """Handle secret creation.

        Either creates a secret item here, or else begins the extended
        process of creating a secret (such as for SSL certificate
        generation.

        :param order: Order to process on behalf of.
        """
        order_info = order.to_dict_fields()
        secret_info = order_info['secret']

        # Retrieve the tenant.
        tenant = self.tenant_repo.get(order.tenant_id)

        # Create Secret
        new_secret = res.create_secret(secret_info, tenant,
                                       self.crypto_manager, self.secret_repo,
                                       self.tenant_secret_repo,
                                       self.datum_repo, self.kek_repo,
                                       ok_to_generate=True)
        order.secret_id = new_secret.id

        LOG.debug("...done creating order's secret.")


class PerformVerification(BaseTask):
    """Handles beginning processing a Verification request."""

    def get_name(self):
        return u._('Perform Verification')

    def __init__(self, nova_client, tenant_repo=None,
                 verification_repo=None,
                 verification_expected_repo=None):
        LOG.debug('Creating PerformVerification task processor')
        self.tenant_repo = tenant_repo or rep.TenantRepo()
        self.verification_repo = verification_repo or rep.VerificationRepo()
        self.verification_expected_repo = verification_expected_repo or \
            rep.VerificationExpectedDatumRepo()
        self.nova = nova_client

    def retrieve_entity(self, verification_id, keystone_id):
        return self.verification_repo.get(entity_id=verification_id,
                                          keystone_id=keystone_id)

    def handle_processing(self, verification, *args, **kwargs):
        self.handle_verification(verification)

    def handle_error(self, verification, status, message, exception,
                     task_will_be_retried, *args, **kwargs):
        status, message = self._refine_error(status, message, exception)

        verification.status = models.States.PENDING if task_will_be_retried \
            else models.States.ERROR
        verification.error_status_code = status
        verification.error_reason = message
        self.verification_repo.save(verification)

    def handle_success(self, verification, *args, **kwargs):
        verification.status = models.States.ACTIVE
        self.verification_repo.save(verification)

    def handle_verification(self, verification):
        """Handle performing a verification.

        Performs a verification process on a reference.

        :param verification: Verification to process on behalf of.
        """
        # Perform the verification.
        LOG.debug("Begin resource verification")

        verification.is_verified = self._handle_image_verification(
            verification)

        LOG.debug("...done verifying resource.")

    def _handle_image_verification(self, verification):
        """Image Verification logic."""
        # Retrieve the tenant.
        tenant = self.tenant_repo.get(verification.tenant_id)

        # First we retrieve the expected data
        expected_datum = self.verification_expected_repo.get_by_keystone_id(
            tenant.keystone_id
        )
        expected = expected_datum.json_payload

        # Retrieve server details for the server being spun up
        server_details = self.nova \
            .get_server_details(verification.openstack_meta_data['uuid'])
        if not self._verify_server_details(verification, expected,
                                           server_details):
            return False

        # Retrieve server actions for the server being spun up
        server_actions = self.nova \
            .get_server_actions(verification.openstack_meta_data['uuid'])
        if not self._verify_server_actions(verification, expected,
                                           server_actions):
            return False

        # Cross-check data common between server details and actions.
        if not self._verify_server_common_data(server_details,
                                               server_actions):
            return False

        # Retrieve config data from config api ???
        #TODO(dmend): Not sure what the config api is >_<

        return True

    def _verify_server_details(self, verification, expected, server_details):
        """Verify the expected server details match actual."""
        if 'ACTIVE' != server_details.status:
            raise NovaServerNotReadyException()

        LOG.debug("Server details: {0}".format(server_details))
        for key, value in server_details.__dict__.iteritems():
            LOG.debug('    {0}: {1}'.format(key, value))
        LOG.debug('server details for {0}'.format(server_details.id))

        # Match IPv4 address.
        #TODO(jwood) Verify that the public/private IP swap bug is fixed.
        ip_addr = verification.ec2_meta_data.get('public-ipv4')
        if not ip_addr:
            ip_addr = verification.ec2_meta_data.get('local-ipv4', '')
        if not self._compare(
                '[Server Details] IPv4 mismatch seen',
                ip_addr,
                server_details.accessIPv4 or self._get_ipv4(
                    server_details.addresses['public']
                )):
            return False

        # Match flavor.
        if not self._compare('[Server Details] Flavor mismatch seen',
                             expected['server_details']['flavor'],
                             server_details.flavor['id']):
            return False

        # Match tenant ID.
        if not self._compare('[Server Details] Project ID mismatch seen',
                             expected['project_id'],
                             server_details.tenant_id):
            return False

        # Log Image ID used by VM
        LOG.info('Image ID used for server: {0}'.format(
            server_details.image['id']
        ))

        #TODO(jwood) Check 'created' date isn't too old.

        return True

    def _verify_server_actions(self, verification, expected, server_actions):
        """Verify the expected server details match actual."""
        LOG.debug("Server actions: {0}".format(server_actions))
        LOG.debug('verifying {0} server actions.'.format(len(server_actions)))

        # Fail if no server actions seen.
        if not server_actions:
            LOG.warn('Expected one or more server actions to be provided')
            return False

        # No more than max events expected.
        max_expected = int(expected.get('max_actions_allowed', 0))
        if 0 < max_expected < len(server_actions):
            LOG.warn(''.join(['[Server Actions] Expected no more than '
                              '{0} actions'.format(max_expected),
                              ' - {0} > {1}'.format(len(server_actions),
                                                    max_expected)]))
            return False

        # Match action name.
        create_cnt = 0
        for action in server_actions:
            if 'create' == action.action:
                create_cnt += 1
        if create_cnt != 1:
            LOG.warn("Expected one server 'create' action to be "
                     "provided - {0} were seen".format(create_cnt))
            return False

        # Match project ID.
        if not self._compare('[Server Actions] Project ID mismatch seen',
                             expected['project_id'],
                             server_actions[0].project_id):
            return False

        return True

    def _verify_server_common_data(self, server_details, server_actions):
        """Verify data common between server details and actions."""
        # Match user ID.
        if not self._compare('[Server Common] Details and actions '
                             'user_id mismatch seen',
                             server_details.user_id,
                             server_actions[0].user_id):
            return False

        # Match instance_uuid.
        if not self._compare('[Server Common] Details and actions '
                             'instance UUID mismatch seen',
                             server_details.id,
                             server_actions[0].instance_uuid):
            return False

        return True

    def _compare(self, error_msg, left, right):
        if left != right:
            LOG.warn(''.join([error_msg,
                              ' - {0} != {1}'.format(left, right)]))
            return False

        return True

    def _get_ipv4(self, addresses):
        """Retrieves IPv4 address from a list of address dicts"""
        for addr in addresses:
            if addr['version'] == 4:
                return addr['addr']
        LOG.warn("IPv4 address not found in addresses")

    def _refine_error(self, status, message, exception):
        """Optional override of reported error status."""
        if '500' != status:
            return status, message

        if isinstance(exception, NovaServerNotReadyException):
            return '503', u._("Nova server is not ready for verification "
                              "as it is still building")
        elif isinstance(exception, requests.ConnectionError):
            return '504', u._("Timeout seen trying to access "
                              "Nova admin endpoint")
        elif isinstance(exception, nex.NotFound):
            return '404', u._("Could not locate requested server in Nova")
        elif isinstance(exception, nex.OverLimit):
            return '503', u._("Nova admin accesses are over limit")
        elif isinstance(exception, nex.RateLimit):
            return '503', u._("Nova admin accesses are rate limited")

        return status, message
