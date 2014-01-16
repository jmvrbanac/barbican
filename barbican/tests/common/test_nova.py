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
import unittest

import mock

from barbican.common import nova


class WhenTestingNovaClient(unittest.TestCase):

    def setUp(self):
        self.image_id = 'image_id'
        self.client = nova.NovaClient()

    def test_get_server_details_delegates_to_novaclient(self):
        self.client._client.servers.get = mock.MagicMock()

        self.client.get_server_details(self.image_id)

        self.client._client.servers.get.assert_called_with(self.image_id)

    def test_get_server_actions_delegates_to_novaclient(self):
        self.client._client.instance_action.list = mock.MagicMock()

        self.client.get_server_actions(self.image_id)

        self.client._client.instance_action.list.assert_called_with(
            self.image_id
        )
