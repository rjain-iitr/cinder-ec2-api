# Copyright 2014
# The Cloudscaling Group, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import imp

from keystoneclient.v2_0 import client as keystone_client_v2
from keystoneclient.v3 import client as keystone_client_v3
import mock
from oslo_config import cfg
from oslo_config import fixture as config_fixture
from oslo_context import context
from oslotest import base as test_base

from ec2api import context as ec2_context
from ec2api import exception

cfg.CONF.import_opt('keystone_url', 'ec2api.api')


class ContextTestCase(test_base.BaseTestCase):

    def setUp(self):
        super(ContextTestCase, self).setUp()
        conf = config_fixture.Config()
        conf.config(admin_user='admin',
                    admin_password='password',
                    admin_tenant_name='service')

    @mock.patch('keystoneclient.auth.identity.generic.password.Password')
    def test_get_os_admin_context(self, password_plugin):
        imp.reload(ec2_context)
        # NOTE(ft): initialize a regular context to populate oslo_context's
        # local storage to prevent admin context to populate it.
        # Used to implicitly validate overwrite=False argument of the call
        # RequestContext constructor from inside get_os_admin_context
        if not context.get_current():
            ec2_context.RequestContext(None, None)

        ctx = ec2_context.get_os_admin_context()
        conf = cfg.CONF
        password_plugin.assert_called_once_with(
            username=conf.admin_user,
            password=conf.admin_password,
            tenant_name=conf.admin_tenant_name,
            project_name=conf.admin_tenant_name,
            auth_url=conf.keystone_url)
        self.assertIsNone(ctx.user_id)
        self.assertIsNone(ctx.project_id)
        self.assertIsNone(ctx.auth_token)
        self.assertEqual([], ctx.service_catalog)
        self.assertTrue(ctx.is_os_admin)
        self.assertIsNotNone(ctx.session)
        self.assertIsNotNone(ctx.session.auth)
        self.assertNotEqual(context.get_current(), ctx)

        password_plugin.reset_mock()
        self.assertEqual(ctx, ec2_context.get_os_admin_context())
        self.assertFalse(password_plugin.called)

    @mock.patch('keystoneclient.client.Client')
    def test_get_keystone_client_class(self, client):
        client.return_value = mock.MagicMock(spec=keystone_client_v2.Client)
        ec2_context._keystone_client_class = None
        client_class = ec2_context.get_keystone_client_class()
        client.assert_called_once_with(auth_url='http://localhost:5000/v2.0')
        self.assertEqual(keystone_client_v2.Client, client_class)
        client.reset_mock()

        client.return_value = mock.MagicMock(spec=keystone_client_v3.Client)
        ec2_context._keystone_client_class = None
        client_class = ec2_context.get_keystone_client_class()
        client.assert_called_once_with(auth_url='http://localhost:5000/v2.0')
        self.assertEqual(keystone_client_v3.Client, client_class)
        client.reset_mock()

        client.return_value = mock.MagicMock()
        ec2_context._keystone_client_class = None
        self.assertRaises(exception.EC2KeystoneDiscoverFailure,
                          ec2_context.get_keystone_client_class)
