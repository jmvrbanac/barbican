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
Client for Nova service.
"""
from novaclient import client as nova_client
from novaclient import extension
from novaclient.v1_1.contrib import instance_action
from oslo.config import cfg

from barbican.common import utils


LOG = utils.getLogger(__name__)

_DEFAULT_AUTH_URL = 'http://localhost:5000/v2.0'
_DEFAULT_BYPASS_URL = 'http://localhost:8774'
_DEFAULT_CLIENT_VERSION = '2'

opt_group = cfg.OptGroup(name='nova',
                         title='Configuration for nova client.')
nova_opts = [
    cfg.StrOpt('auth_url', default=_DEFAULT_AUTH_URL,
               help='Authentication endpoint for Nova.'),
    cfg.StrOpt('bypass_url', default=_DEFAULT_BYPASS_URL,
               help='Bypass url for service.'),
    cfg.StrOpt('username', default='username',
               help='Username for barbican cloud account.'),
    cfg.StrOpt('password', default='password',
               help='Password for barbican cloud account.'),
    cfg.StrOpt('project', default='project',
               help='Project ID for barbican cloud account a.k.a. tenant.'),
    cfg.BoolOpt('insecure_client', default=False,
                help='Allow insecure connections to Nova.')
]

CONF = cfg.CONF
CONF.register_group(opt_group)
CONF.register_opts(nova_opts, opt_group)


class NovaClient():
    """Nova Client facade for barbican use."""
    def __init__(self):
        extensions = [
            extension.Extension(instance_action.__name__.split('.')[-1],
                                instance_action)
        ]
        self._client = nova_client.Client(_DEFAULT_CLIENT_VERSION,
                                          CONF.nova.username,
                                          CONF.nova.password,
                                          CONF.nova.project,
                                          auth_url=CONF.nova.auth_url,
                                          bypass_url=CONF.nova.bypass_url,
                                          insecure=CONF.nova.insecure_client,
                                          extensions=extensions)

    def get_server_details(self, server_id):
        """
        Get details for a Server.

        :param server_id: The Server ID to get details for.
        """
        LOG.debug('Retrieving server details for {0}'.format(server_id))
        return self._client.servers.get(server_id)

    def get_server_actions(self, server_id):
        """
        Get a list of InstanceActions that have been performed on a server.

        :param server_id: The Server ID to get actions for.
        """
        LOG.debug('Retrieving server actions for {0}'.format(server_id))
        return self._client.instance_action.list(server_id)
