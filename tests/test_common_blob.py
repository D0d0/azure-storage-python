﻿# coding: utf-8

#-------------------------------------------------------------------------
# Copyright (c) Microsoft.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#--------------------------------------------------------------------------
import base64
import os
import random
import requests
import sys
import unittest
from datetime import datetime, timedelta
from azure.common import (
    AzureHttpError,
    AzureConflictHttpError,
    AzureMissingResourceHttpError,
)
from azure.storage import (
    AccessPolicy,
    ResourceTypes,
    AccountPermissions,
)
from azure.storage.blob import (
    Blob,
    BlockBlobService,
    BlobPermissions,
    ContainerPermissions,
    ContentSettings,
)
from tests.common_recordingtestcase import (
    TestMode,
    record,
)
from tests.testcase import StorageTestCase


#------------------------------------------------------------------------------


class StorageCommonBlobTest(StorageTestCase):

    def setUp(self):
        super(StorageCommonBlobTest, self).setUp()

        self.bs = self._create_storage_service(BlockBlobService, self.settings)

        if self.settings.REMOTE_STORAGE_ACCOUNT_NAME and self.settings.REMOTE_STORAGE_ACCOUNT_KEY:
            self.bs2 = self._create_storage_service(
                BlockBlobService,
                self.settings,
                self.settings.REMOTE_STORAGE_ACCOUNT_NAME,
                self.settings.REMOTE_STORAGE_ACCOUNT_KEY,
            )
        else:
            print("REMOTE_STORAGE_ACCOUNT_NAME and REMOTE_STORAGE_ACCOUNT_KEY not set in test settings file.")

        # test chunking functionality by reducing the threshold
        # for chunking and the size of each chunk, otherwise
        # the tests would take too long to execute
        self.bs._BLOB_MAX_DATA_SIZE = 64 * 1024
        self.bs._BLOB_MAX_CHUNK_DATA_SIZE = 4 * 1024

        self.container_name = self.get_resource_name('utcontainer')
        self.additional_container_names = []
        self.remote_container_name = None

    def tearDown(self):
        if not self.is_playback():
            try:
                self.bs.delete_container(self.container_name)
            except AzureHttpError:
                try: 
                    lease_time = self.bs.break_container_lease(self.container_name, 0)
                    self.bs.delete_container(self.container_name)
                except:
                    pass
            except:
                pass

            for name in self.additional_container_names:
                try:
                    self.bs.delete_container(name)
                except:
                    pass

            if self.remote_container_name:
                try:
                    self.bs2.delete_container(self.remote_container_name)
                except:
                    pass

        for tmp_file in ['blob_input.temp.dat', 'blob_output.temp.dat']:
            if os.path.isfile(tmp_file):
                try:
                    os.remove(tmp_file)
                except:
                    pass

        return super(StorageCommonBlobTest, self).tearDown()

    #--Helpers-----------------------------------------------------------------
    def _create_container(self, container_name):
        self.bs.create_container(container_name, None, None, True)

    def _create_container_and_block_blob(self, container_name, blob_name,
                                         blob_data):
        self._create_container(container_name)
        resp = self.bs.create_blob_from_bytes(container_name, blob_name, blob_data)
        self.assertIsNone(resp)

    def _create_container_and_block_blob_with_random_data(
        self, container_name, blob_name, block_count, block_size):

        self._create_container_and_block_blob(container_name, blob_name, '')
        block_list = []
        for i in range(0, block_count):
            block_id = '{0:04d}'.format(i)
            block_data = self._get_random_bytes(block_size)
            self.bs.put_block(container_name, blob_name, block_data, block_id)
            block_list.append(block_id)
        self.bs.put_block_list(container_name, blob_name, block_list)

    def _create_remote_container_and_block_blob(self, source_blob_name, data,
                                                blob_public_access):
        self.remote_container_name = self.get_resource_name('remotectnr')
        self.bs2.create_container(
            self.remote_container_name,
            blob_public_access=blob_public_access)
        self.bs2.create_blob_from_bytes(
            self.remote_container_name, source_blob_name, data)
        source_blob_url = self.bs2.make_blob_url(
            self.remote_container_name, source_blob_name)
        return source_blob_url

    def _wait_for_async_copy(self, container_name, blob_name):
        count = 0
        blob = self.bs.get_blob_properties(container_name, blob_name)
        while blob.properties.copy.status != 'success':
            count = count + 1
            if count > 5:
                self.assertTrue(
                    False, 'Timed out waiting for async copy to complete.')
            self.sleep(5)
            blob = self.bs.get_blob_properties(container_name, blob_name)
        self.assertEqual(blob.properties.copy.status, 'success')

    def assertBlobEqual(self, container_name, blob_name, expected_data):
        actual_data = self.bs.get_blob_to_bytes(container_name, blob_name)
        self.assertEqual(actual_data.content, expected_data)

    def assertBlobLengthEqual(self, container_name, blob_name, expected_length):
        blob = self.bs.get_blob_properties(container_name, blob_name)
        self.assertEqual(int(blob.properties.content_length), expected_length)

    def _get_oversized_binary_data(self):
        '''Returns random binary data exceeding the size threshold for
        chunking blob upload.'''
        size = self.bs._BLOB_MAX_DATA_SIZE + 12345
        return self._get_random_bytes(size)

    def _get_expected_progress(self, blob_size, unknown_size=True):
        result = []
        index = 0
        if unknown_size:
            result.append((0, None))
        else:
            while (index < blob_size):
                result.append((index, blob_size))
                index += self.bs._BLOB_MAX_CHUNK_DATA_SIZE
        result.append((blob_size, blob_size))
        return result

    def _get_random_bytes(self, size):
        # Must not be really random, otherwise playback of recordings
        # won't work. Data must be randomized, but the same for each run.
        # Use the checksum of the qualified test name as the random seed.
        rand = random.Random(self.checksum)
        result = bytearray(size)
        for i in range(size):
            result[i] = rand.randint(0, 255)
        return bytes(result)

    def _get_oversized_text_data(self):
        '''Returns random unicode text data exceeding the size threshold for
        chunking blob upload.'''
        # Must not be really random, otherwise playback of recordings
        # won't work. Data must be randomized, but the same for each run.
        # Use the checksum of the qualified test name as the random seed.
        rand = random.Random(self.checksum)
        size = self.bs._BLOB_MAX_DATA_SIZE + 12345
        text = u''
        words = [u'hello', u'world', u'python', u'啊齄丂狛狜']
        while (len(text) < size):
            index = rand.randint(0, len(words) - 1)
            text = text + u' ' + words[index]

        return text

    class NonSeekableFile(object):
        def __init__(self, wrapped_file):
            self.wrapped_file = wrapped_file

        def write(self, data):
            self.wrapped_file.write(data)

        def read(self, count):
            return self.wrapped_file.read(count)
        
    #--Test cases for containers -----------------------------------------
    @record
    def test_create_container_no_options(self):
        # Arrange

        # Act
        created = self.bs.create_container(self.container_name)

        # Assert
        self.assertTrue(created)

    @record
    def test_create_container_no_options_fail_on_exist(self):
        # Arrange

        # Act
        created = self.bs.create_container(
            self.container_name, None, None, True)

        # Assert
        self.assertTrue(created)

    @record
    def test_create_container_with_already_existing_container(self):
        # Arrange

        # Act
        created1 = self.bs.create_container(self.container_name)
        created2 = self.bs.create_container(self.container_name)

        # Assert
        self.assertTrue(created1)
        self.assertFalse(created2)

    @record
    def test_create_container_with_already_existing_container_fail_on_exist(self):
        # Arrange

        # Act
        created = self.bs.create_container(self.container_name)
        with self.assertRaises(AzureConflictHttpError):
            self.bs.create_container(self.container_name, None, None, True)

        # Assert
        self.assertTrue(created)

    @record
    def test_create_container_with_public_access_container(self):
        # Arrange

        # Act
        created = self.bs.create_container(
            self.container_name, None, 'container')

        # Assert
        self.assertTrue(created)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_create_container_with_public_access_blob(self):
        # Arrange

        # Act
        created = self.bs.create_container(self.container_name, None, 'blob')

        # Assert
        self.assertTrue(created)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_create_container_with_metadata(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42'}

        # Act
        created = self.bs.create_container(self.container_name, metadata)

        # Assert
        self.assertTrue(created)
        md = self.bs.get_container_metadata(self.container_name)
        self.assertDictEqual(md, metadata)

    @record
    def test_container_exists(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        exists = self.bs.exists(self.container_name)

        # Assert
        self.assertTrue(exists)

    @record
    def test_container_not_exists(self):
        # Arrange

        # Act
        exists = self.bs.exists(self.get_resource_name('missing'))

        # Assert
        self.assertFalse(exists)

    @record
    def test_container_exists_with_lease(self):
        # Arrange
        self.bs.create_container(self.container_name)
        self.bs.acquire_container_lease(self.container_name)

        # Act
        exists = self.bs.exists(self.container_name)

        # Assert
        self.assertTrue(exists)

    @record
    def test_list_containers_no_options(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        result = self.bs.list_containers()
        containers = result
        while result.next_marker:
            result = self.bs.list_containers(marker=result.next_marker)
            containers += result

        # Assert
        self.assertIsNotNone(containers)
        self.assertGreaterEqual(len(containers), 1)
        self.assertIsNotNone(containers[0])
        self.assertNamedItemInContainer(containers, self.container_name)

    @record
    def test_list_containers_with_prefix(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        containers = self.bs.list_containers(self.container_name)

        # Assert
        self.assertIsNotNone(containers)
        self.assertEqual(len(containers), 1)
        self.assertIsNotNone(containers[0])
        self.assertEqual(containers[0].name, self.container_name)
        self.assertIsNone(containers[0].metadata)

    @record
    def test_list_containers_with_include_metadata(self):
        # Arrange
        self.bs.create_container(self.container_name)
        resp = self.bs.set_container_metadata(
            self.container_name, {'hello': 'world', 'number': '43'})

        # Act
        containers = self.bs.list_containers(
            self.container_name, None, None, 'metadata')

        # Assert
        self.assertIsNotNone(containers)
        self.assertGreaterEqual(len(containers), 1)
        self.assertIsNotNone(containers[0])
        self.assertNamedItemInContainer(containers, self.container_name)
        self.assertEqual(containers[0].metadata['hello'], 'world')
        self.assertEqual(containers[0].metadata['number'], '43')

    @record
    def test_list_containers_with_maxresults_and_marker(self):
        # Arrange
        self.additional_container_names = [self.container_name + 'a',
                                           self.container_name + 'b',
                                           self.container_name + 'c',
                                           self.container_name + 'd']
        for name in self.additional_container_names:
            self.bs.create_container(name)

        # Act
        containers1 = self.bs.list_containers(self.container_name, None, 2)
        containers2 = self.bs.list_containers(
            self.container_name, containers1.next_marker, 2)

        # Assert
        self.assertIsNotNone(containers1)
        self.assertEqual(len(containers1), 2)
        self.assertNamedItemInContainer(containers1, self.container_name + 'a')
        self.assertNamedItemInContainer(containers1, self.container_name + 'b')
        self.assertIsNotNone(containers2)
        self.assertEqual(len(containers2), 2)
        self.assertNamedItemInContainer(containers2, self.container_name + 'c')
        self.assertNamedItemInContainer(containers2, self.container_name + 'd')

    @record
    def test_set_container_metadata(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '43'}
        self.bs.create_container(self.container_name)

        # Act
        resp = self.bs.set_container_metadata(self.container_name, metadata)

        # Assert
        self.assertIsNone(resp)
        md = self.bs.get_container_metadata(self.container_name)
        self.assertDictEqual(md, metadata)

    @record
    def test_set_container_metadata_with_lease_id(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '43'}
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(self.container_name)

        # Act
        resp = self.bs.set_container_metadata(self.container_name, metadata, lease_id)

        # Assert
        self.assertIsNone(resp)
        md = self.bs.get_container_metadata(self.container_name)
        self.assertDictEqual(md, metadata)

    @record
    def test_set_container_metadata_with_lease_id_fail(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease = self.bs.acquire_container_lease(self.container_name)

        # Act
        non_matching_lease_id = '00000000-1111-2222-3333-444444444444'
        with self.assertRaises(AzureHttpError):
            self.bs.set_container_metadata(
                self.container_name,
                {'hello': 'world', 'number': '43'},
                non_matching_lease_id)

        # Assert

    @record
    def test_set_container_metadata_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.set_container_metadata(
                self.container_name, {'hello': 'world', 'number': '43'})

        # Assert

    @record
    def test_get_container_metadata(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42'}
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(self.container_name, metadata)

        # Act
        md = self.bs.get_container_metadata(self.container_name)

        # Assert
        self.assertDictEqual(md, metadata)

    @record
    def test_get_container_metadata_with_lease_id(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42'}
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(self.container_name, metadata)
        lease_id = self.bs.acquire_container_lease(self.container_name)

        # Act
        md = self.bs.get_container_metadata(self.container_name, lease_id)

        # Assert
        self.assertDictEqual(md, metadata)

    @record
    def test_get_container_metadata_with_non_matching_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(
            self.container_name, {'hello': 'world', 'number': '42'})
        lease = self.bs.acquire_container_lease(self.container_name)

        # Act
        non_matching_lease_id = '00000000-1111-2222-3333-444444444444'
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_metadata(
                self.container_name, non_matching_lease_id)

        # Assert

    @record
    def test_get_container_metadata_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_metadata(self.container_name)

        # Assert

    @record
    def test_get_container_properties(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42'}
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(self.container_name, metadata)

        # Act
        props = self.bs.get_container_properties(self.container_name)

        # Assert
        self.assertIsNotNone(props)
        self.assertDictEqual(props.metadata, metadata)
        self.assertEqual(props.properties.lease.duration, 'infinite')
        self.assertEqual(props.properties.lease.state, 'leased')
        self.assertEqual(props.properties.lease.status, 'locked')

    @record
    def test_get_container_properties_with_lease_id(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42'}
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(self.container_name, metadata)
        lease_id = self.bs.acquire_container_lease(self.container_name)

        # Act
        props = self.bs.get_container_properties(self.container_name, lease_id)
        self.bs.break_container_lease(self.container_name)

        # Assert
        self.assertIsNotNone(props)
        self.assertDictEqual(props.metadata, metadata)
        self.assertEqual(props.properties.lease.duration, 'infinite')
        self.assertEqual(props.properties.lease.state, 'leased')
        self.assertEqual(props.properties.lease.status, 'locked')

    @record
    def test_get_container_properties_with_non_matching_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        self.bs.set_container_acl(self.container_name, None, 'container')
        self.bs.set_container_metadata(
            self.container_name, {'hello': 'world', 'number': '42'})
        self.bs.acquire_container_lease(self.container_name)

        # Act
        non_matching_lease_id = '00000000-1111-2222-3333-444444444444'
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_properties(
                self.container_name, non_matching_lease_id)

        # Assert

    @record
    def test_get_container_properties_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_properties(self.container_name)

        # Assert

    @record
    def test_get_container_acl(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        acl = self.bs.get_container_acl(self.container_name)

        # Assert
        self.assertIsNotNone(acl)
        self.assertEqual(len(acl), 0)

    @record
    def test_get_container_acl_with_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(self.container_name)

        # Act
        acl = self.bs.get_container_acl(self.container_name, lease_id)

        # Assert
        self.assertIsNotNone(acl)
        self.assertEqual(len(acl), 0)

    @record
    def test_get_container_acl_with_non_matching_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease = self.bs.acquire_container_lease(self.container_name)

        # Act
        non_matching_lease_id = '00000000-1111-2222-3333-444444444444'
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_acl(
                self.container_name, non_matching_lease_id)

        # Assert

    @record
    def test_get_container_acl_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_container_acl(self.container_name)

        # Assert

    @record
    def test_set_container_acl(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        resp = self.bs.set_container_acl(self.container_name)

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_set_container_acl_with_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(self.container_name)

        # Act
        resp = self.bs.set_container_acl(self.container_name, lease_id=lease_id)

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_set_container_acl_with_non_matching_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease = self.bs.acquire_container_lease(self.container_name)

        # Act
        non_matching_lease_id = '00000000-1111-2222-3333-444444444444'
        with self.assertRaises(AzureHttpError):
            self.bs.set_container_acl(
                self.container_name, lease_id=non_matching_lease_id)

        # Assert

    @record
    def test_set_container_acl_with_public_access_container(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        resp = self.bs.set_container_acl(
            self.container_name, None, 'container')

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_set_container_acl_with_public_access_blob(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        resp = self.bs.set_container_acl(self.container_name, None, 'blob')

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)

    @record
    def test_set_container_acl_with_empty_signed_identifiers(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        resp = self.bs.set_container_acl(self.container_name, dict())

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)
        self.assertEqual(len(acl), 0)

    @record
    def test_set_container_acl_with_signed_identifiers(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        identifiers = dict()
        identifiers['testid'] = AccessPolicy(
            permission=ContainerPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),  
            start=datetime.utcnow() - timedelta(minutes=1),  
            )

        resp = self.bs.set_container_acl(self.container_name, identifiers)

        # Assert
        self.assertIsNone(resp)
        acl = self.bs.get_container_acl(self.container_name)
        self.assertIsNotNone(acl)
        self.assertEqual(len(acl), 1)
        self.assertTrue('testid' in acl)

    @record
    def test_set_container_acl_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.set_container_acl(self.container_name, None, 'container')

        # Assert

    @record
    def test_lease_container_acquire_and_release(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        lease_id = self.bs.acquire_container_lease(self.container_name)
        lease = self.bs.release_container_lease(self.container_name, lease_id)

        # Assert

    @record
    def test_lease_container_renew(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(
            self.container_name, lease_duration=15)
        self.sleep(10)

        # Act
        renewed_lease_id = self.bs.renew_container_lease(
            self.container_name, lease_id)

        # Assert
        self.assertEqual(lease_id, renewed_lease_id)
        self.sleep(5)
        with self.assertRaises(AzureHttpError):
            self.bs.delete_container(self.container_name)
        self.sleep(10)
        self.bs.delete_container(self.container_name)

    @record
    def test_lease_container_break_period(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        lease_id = self.bs.acquire_container_lease(
            self.container_name, lease_duration=15)

        # Assert
        self.bs.break_container_lease(self.container_name,
                                      lease_break_period=5)
        self.sleep(6)
        with self.assertRaises(AzureHttpError):
            self.bs.delete_container(self.container_name, lease_id=lease_id)

    @record
    def test_lease_container_break_released_lease_fails(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(self.container_name)
        self.bs.release_container_lease(self.container_name, lease_id)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.break_container_lease(self.container_name)

        # Assert

    @record
    def test_lease_container_with_duration(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        lease_id = self.bs.acquire_container_lease(self.container_name, lease_duration=15)

        # Assert
        with self.assertRaises(AzureHttpError):
            self.bs.acquire_container_lease(self.container_name)
        self.sleep(15)
        self.bs.acquire_container_lease(self.container_name)

    @record
    def test_lease_container_with_proposed_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        proposed_lease_id = '55e97f64-73e8-4390-838d-d9e84a374321'
        lease_id = self.bs.acquire_container_lease(
            self.container_name, proposed_lease_id=proposed_lease_id)

        # Assert
        self.assertEqual(proposed_lease_id, lease_id)

    @record
    def test_lease_container_change_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        lease_id = '29e0b239-ecda-4f69-bfa3-95f6af91464c'
        lease_id1 = self.bs.acquire_container_lease(self.container_name)
        self.bs.change_container_lease(self.container_name,
                                        lease_id1,
                                        proposed_lease_id=lease_id)
        lease_id2 = self.bs.renew_container_lease(self.container_name, lease_id)

        # Assert
        self.assertIsNotNone(lease_id1)
        self.assertIsNotNone(lease_id2)
        self.assertNotEqual(lease_id1, lease_id)
        self.assertEqual(lease_id2, lease_id)

    @record
    def test_delete_container_with_existing_container(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        deleted = self.bs.delete_container(self.container_name)

        # Assert
        self.assertTrue(deleted)
        containers = self.bs.list_containers()
        self.assertNamedItemNotInContainer(containers, self.container_name)

    @record
    def test_delete_container_with_existing_container_fail_not_exist(self):
        # Arrange
        self.bs.create_container(self.container_name)

        # Act
        deleted = self.bs.delete_container(self.container_name, True)

        # Assert
        self.assertTrue(deleted)
        containers = self.bs.list_containers()
        self.assertNamedItemNotInContainer(containers, self.container_name)

    @record
    def test_delete_container_with_non_existing_container(self):
        # Arrange

        # Act
        deleted = self.bs.delete_container(self.container_name)

        # Assert
        self.assertFalse(deleted)

    @record
    def test_delete_container_with_non_existing_container_fail_not_exist(self):
        # Arrange

        # Act
        with self.assertRaises(AzureMissingResourceHttpError):
            self.bs.delete_container(self.container_name, True)

        # Assert

    @record
    def test_delete_container_with_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        lease_id = self.bs.acquire_container_lease(self.container_name, lease_duration=15)

        # Act
        deleted = self.bs.delete_container(self.container_name, lease_id=lease_id)

        # Assert
        self.assertTrue(deleted)
        containers = self.bs.list_containers()
        self.assertNamedItemNotInContainer(containers, self.container_name)

    @record
    def test_delete_container_without_lease_id(self):
        # Arrange
        self.bs.create_container(self.container_name)
        self.bs.acquire_container_lease(self.container_name, lease_duration=15)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.delete_container(self.container_name)

        # Assert

    #-- Common test cases for blobs ----------------------------------------------
    @record
    def test_blob_exists(self):
        # Arrange
        self._create_container(self.container_name)
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', b'hello world')

        # Act
        exists = self.bs.exists(self.container_name, 'blob1')

        # Assert
        self.assertTrue(exists)

    @record
    def test_blob_not_exists(self):
        # Arrange

        # Act
        exists = self.bs.exists(self.get_resource_name('missing'), 'blob1')

        # Assert
        self.assertFalse(exists)

    @record
    def test_make_blob_url(self):
        # Arrange

        # Act
        res = self.bs.make_blob_url('vhds', 'my.vhd')

        # Assert
        self.assertEqual(res, 'https://' + self.settings.STORAGE_ACCOUNT_NAME
                         + '.blob.core.windows.net/vhds/my.vhd')

    @record
    def test_make_blob_url_with_protocol(self):
        # Arrange

        # Act
        res = self.bs.make_blob_url('vhds', 'my.vhd', protocol='http')

        # Assert
        self.assertEqual(res, 'http://' + self.settings.STORAGE_ACCOUNT_NAME
                         + '.blob.core.windows.net/vhds/my.vhd')

    @record
    def test_make_blob_url_with_sas(self):
        # Arrange

        # Act
        res = self.bs.make_blob_url('vhds', 'my.vhd', sas_token='sas')

        # Assert
        self.assertEqual(res, 'https://' + self.settings.STORAGE_ACCOUNT_NAME
                         + '.blob.core.windows.net/vhds/my.vhd?sas')

    @record
    def test_list_blobs(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'blob2', data, )

        # Act
        resp = self.bs.list_blobs(self.container_name)
        for blob in resp:
            name = blob.name

        # Assert
        self.assertIsNotNone(resp)
        self.assertGreaterEqual(len(resp), 2)
        self.assertIsNotNone(resp[0])
        self.assertNamedItemInContainer(resp, 'blob1')
        self.assertNamedItemInContainer(resp, 'blob2')
        self.assertEqual(resp[0].properties.content_length, 11)
        self.assertEqual(resp[1].properties.content_type,
                         'application/octet-stream')

    @record
    def test_list_blobs_leased_blob(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data, )
        lease = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        resp = self.bs.list_blobs(self.container_name)
        for blob in resp:
            name = blob.name

        # Assert
        self.assertIsNotNone(resp)
        self.assertGreaterEqual(len(resp), 1)
        self.assertIsNotNone(resp[0])
        self.assertNamedItemInContainer(resp, 'blob1')
        self.assertEqual(resp[0].properties.content_length, 11)
        self.assertEqual(resp[0].properties.lease_duration, 'infinite')
        self.assertEqual(resp[0].properties.lease_status, 'locked')
        self.assertEqual(resp[0].properties.lease_state, 'leased')

    @record
    def test_list_blobs_with_prefix(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'bloba1', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'bloba2', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'blobb1', data, )

        # Act
        resp = self.bs.list_blobs(self.container_name, 'bloba')

        # Assert
        self.assertIsNotNone(resp)
        self.assertEqual(len(resp), 2)
        self.assertEqual(len(resp.blobs), 2)
        self.assertEqual(len(resp.prefixes), 0)
        self.assertNamedItemInContainer(resp, 'bloba1')
        self.assertNamedItemInContainer(resp, 'bloba2')

    @record
    def test_list_blobs_with_max_results(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'bloba1', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'bloba2', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'bloba3', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'blobb1', data, )

        # Act
        blobs = self.bs.list_blobs(self.container_name, None, None, 2)

        # Assert
        self.assertIsNotNone(blobs)
        self.assertEqual(len(blobs), 2)
        self.assertNamedItemInContainer(blobs, 'bloba1')
        self.assertNamedItemInContainer(blobs, 'bloba2')

    @record
    def test_list_blobs_with_max_results_and_marker(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'bloba1', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'bloba2', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'bloba3', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'blobb1', data, )

        # Act
        blobs1 = self.bs.list_blobs(self.container_name, None, None, 2)
        blobs2 = self.bs.list_blobs(
            self.container_name, None, blobs1.next_marker, 2)

        # Assert
        self.assertEqual(len(blobs1), 2)
        self.assertEqual(len(blobs2), 2)
        self.assertNamedItemInContainer(blobs1, 'bloba1')
        self.assertNamedItemInContainer(blobs1, 'bloba2')
        self.assertNamedItemInContainer(blobs2, 'bloba3')
        self.assertNamedItemInContainer(blobs2, 'blobb1')

    @record
    def test_list_blobs_with_include_snapshots(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data, )
        self.bs.create_blob_from_bytes (self.container_name, 'blob2', data, )
        self.bs.snapshot_blob(self.container_name, 'blob1')

        # Act
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')

        # Assert
        self.assertEqual(len(blobs), 3)
        self.assertEqual(blobs[0].name, 'blob1')
        self.assertIsNotNone(blobs[0].snapshot)
        self.assertEqual(blobs[1].name, 'blob1')
        self.assertIsNone(blobs[1].snapshot)
        self.assertEqual(blobs[2].name, 'blob2')
        self.assertIsNone(blobs[2].snapshot)

    @record
    def test_list_blobs_with_include_metadata(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data,
                         metadata={'number': '1', 'name': 'bob'})
        self.bs.create_blob_from_bytes (self.container_name, 'blob2', data,
                         metadata={'number': '2', 'name': 'car'})
        self.bs.snapshot_blob(self.container_name, 'blob1')

        # Act
        blobs = self.bs.list_blobs(self.container_name, include='metadata')

        # Assert
        self.assertEqual(len(blobs), 2)
        self.assertEqual(blobs[0].name, 'blob1')
        self.assertEqual(blobs[0].metadata['number'], '1')
        self.assertEqual(blobs[0].metadata['name'], 'bob')
        self.assertEqual(blobs[1].name, 'blob2')
        self.assertEqual(blobs[1].metadata['number'], '2')
        self.assertEqual(blobs[1].metadata['name'], 'car')

    @record
    def test_list_blobs_with_include_uncommittedblobs(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.put_block(self.container_name, 'blob1', b'AAA', '1')
        self.bs.put_block(self.container_name, 'blob1', b'BBB', '2')
        self.bs.put_block(self.container_name, 'blob1', b'CCC', '3')
        self.bs.create_blob_from_bytes (self.container_name, 'blob2', data,
                         metadata={'number': '2', 'name': 'car'})

        # Act
        blobs = self.bs.list_blobs(
            self.container_name, include='uncommittedblobs')

        # Assert
        self.assertEqual(len(blobs), 2)
        self.assertEqual(blobs[0].name, 'blob1')
        self.assertEqual(blobs[1].name, 'blob2')

    @record
    def test_list_blobs_with_include_copy(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data,
                         metadata={'status': 'original'})
        sourceblob = 'https://{0}.blob.core.windows.net/{1}/{2}'.format(
            self.settings.STORAGE_ACCOUNT_NAME,
            self.container_name,
            'blob1')
        self.bs.copy_blob(self.container_name, 'blob1copy',
                          sourceblob, {'status': 'copy'})

        # Act
        blobs = self.bs.list_blobs(self.container_name, include='copy')

        # Assert
        self.assertEqual(len(blobs), 2)
        self.assertEqual(blobs[0].name, 'blob1')
        self.assertEqual(blobs[1].name, 'blob1copy')
        self.assertEqual(blobs[1].properties.content_length, 11)
        self.assertEqual(blobs[1].properties.content_type,
                         'application/octet-stream')
        self.assertEqual(blobs[1].properties.content_encoding, None)
        self.assertEqual(blobs[1].properties.content_language, None)
        self.assertNotEqual(blobs[1].properties.content_md5, None)
        self.assertEqual(blobs[1].properties.blob_type, self.bs.blob_type)
        self.assertEqual(blobs[1].properties.lease_status, 'unlocked')
        self.assertEqual(blobs[1].properties.lease_state, 'available')
        self.assertNotEqual(blobs[1].properties.copy_id, None)
        self.assertEqual(blobs[1].properties.copy_source, sourceblob)
        self.assertEqual(blobs[1].properties.copy_status, 'success')
        self.assertEqual(blobs[1].properties.copy_progress, '11/11')
        self.assertNotEqual(blobs[1].properties.copy_completion_time, None)

    @record
    def test_list_blobs_with_include_multiple(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, 'blob1', data,
                         metadata={'number': '1', 'name': 'bob'})
        self.bs.create_blob_from_bytes (self.container_name, 'blob2', data,
                         metadata={'number': '2', 'name': 'car'})
        self.bs.snapshot_blob(self.container_name, 'blob1')

        # Act
        blobs = self.bs.list_blobs(
            self.container_name, include='snapshots,metadata')

        # Assert
        self.assertEqual(len(blobs), 3)
        self.assertEqual(blobs[0].name, 'blob1')
        self.assertIsNotNone(blobs[0].snapshot)
        self.assertEqual(blobs[0].metadata['number'], '1')
        self.assertEqual(blobs[0].metadata['name'], 'bob')
        self.assertEqual(blobs[1].name, 'blob1')
        self.assertIsNone(blobs[1].snapshot)
        self.assertEqual(blobs[1].metadata['number'], '1')
        self.assertEqual(blobs[1].metadata['name'], 'bob')
        self.assertEqual(blobs[2].name, 'blob2')
        self.assertIsNone(blobs[2].snapshot)
        self.assertEqual(blobs[2].metadata['number'], '2')
        self.assertEqual(blobs[2].metadata['name'], 'car')

    @record
    def test_create_blob_with_question_mark(self):
        # Arrange
        self._create_container(self.container_name)
        blob_name = '?ques?tion?'
        blob_data = u'???'

        # Act
        self.bs.create_blob_from_text(self.container_name, blob_name, blob_data)

        # Assert
        blob = self.bs.get_blob_to_text(self.container_name, blob_name)
        self.assertEqual(blob.content, blob_data)

    @record
    def test_create_blob_with_special_chars(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        for c in '-._ /()$=\',~':
            blob_name = '{0}a{0}a{0}'.format(c)
            blob_data = c
            self.bs.create_blob_from_text(self.container_name, blob_name, blob_data)
            blob = self.bs.get_blob_to_text(self.container_name, blob_name)
            self.assertEqual(blob.content, blob_data)

        # Assert

    @record
    def test_create_blob_with_lease_id(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        data = b'hello world again'
        resp = self.bs.create_blob_from_bytes (
            self.container_name, 'blob1', data,
            lease_id=lease_id)

        # Assert
        self.assertIsNone(resp)
        blob = self.bs.get_blob_to_bytes(
            self.container_name, 'blob1', lease_id=lease_id)
        self.assertEqual(blob.content, b'hello world again')

    @record
    def test_create_blob_with_metadata(self):
        # Arrange
        metadata={'hello': 'world', 'number': '42'}
        self._create_container(self.container_name)

        # Act
        data = b'hello world'
        resp = self.bs.create_blob_from_bytes(
            self.container_name, 'blob1', data, metadata=metadata)

        # Assert
        self.assertIsNone(resp)
        md = self.bs.get_blob_metadata(self.container_name, 'blob1')
        self.assertDictEqual(md, metadata)

    @record
    def test_get_blob_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello world')

    @record
    def test_get_blob_with_snapshot(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        snapshot = self.bs.snapshot_blob(self.container_name, 'blob1')

        # Act
        blob = self.bs.get_blob_to_bytes(
            self.container_name, 'blob1', snapshot.snapshot)

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello world')

    @record
    def test_get_blob_with_snapshot_previous(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        snapshot = self.bs.snapshot_blob(self.container_name, 'blob1')
        self.bs.create_blob_from_bytes (self.container_name, 'blob1',
                         b'hello world again', )

        # Act
        blob_previous = self.bs.get_blob_to_bytes(
            self.container_name, 'blob1', snapshot.snapshot)
        blob_latest = self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob_previous, Blob)
        self.assertIsInstance(blob_latest, Blob)
        self.assertEqual(blob_previous.content, b'hello world')
        self.assertEqual(blob_latest.content, b'hello world again')

    @record
    def test_get_blob_with_range(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        blob = self.bs.get_blob_to_bytes(
            self.container_name, 'blob1', start_range=0, end_range=5)

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello ')

    @record
    def test_get_blob_with_range_and_get_content_md5(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1',
                                start_range=0, end_range=5,
                                range_get_content_md5=True)

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello ')
        self.assertEqual(
            blob.properties.content_settings.content_md5, '+BSJN3e8wilf/wXwDlCNpg==')

    @record
    def test_get_blob_with_lease(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        blob = self.bs.get_blob_to_bytes(
            self.container_name, 'blob1', lease_id=lease_id)
        self.bs.release_blob_lease(self.container_name, 'blob1', lease_id)

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello world')

    @record
    def test_get_blob_on_leased_blob_without_lease_id(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        # get_blob_to_bytes is allowed without lease id
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello world')

    @record
    def test_get_blob_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert

    @record
    def test_get_blob_with_non_existing_blob(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert

    @record
    def test_set_blob_properties_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        resp = self.bs.set_blob_properties(
            self.container_name,
            'blob1',
            content_settings=ContentSettings(
                content_language='spanish',
                content_disposition='inline'),
        )

        # Assert
        self.assertIsNone(resp)
        blob = self.bs.get_blob_properties(self.container_name, 'blob1')
        self.assertEqual(blob.properties.content_settings.content_language, 'spanish')
        self.assertEqual(blob.properties.content_settings.content_disposition, 'inline')

    @record
    def test_set_blob_properties_with_blob_settings_param(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        blob = self.bs.get_blob_properties(self.container_name, 'blob1')

        # Act
        blob.properties.content_settings.content_language = 'spanish'
        blob.properties.content_settings.content_disposition = 'inline'
        resp = self.bs.set_blob_properties(
            self.container_name,
            'blob1',
            content_settings=blob.properties.content_settings,
        )

        # Assert
        self.assertIsNone(resp)
        blob = self.bs.get_blob_properties(self.container_name, 'blob1')
        self.assertEqual(blob.properties.content_settings.content_language, 'spanish')
        self.assertEqual(blob.properties.content_settings.content_disposition, 'inline')

    @record
    def test_set_blob_properties_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.set_blob_properties(
                self.container_name, 'blob1',
                content_settings=ContentSettings(content_language='spanish'))

        # Assert

    @record
    def test_set_blob_properties_with_non_existing_blob(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.set_blob_properties(
                self.container_name, 'blob1',
                content_settings=ContentSettings(content_language='spanish'))

        # Assert

    @record
    def test_get_blob_properties_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        blob = self.bs.get_blob_properties(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.properties.blob_type, self.bs.blob_type)
        self.assertEqual(blob.properties.content_length, 11)
        self.assertEqual(blob.properties.lease.status, 'unlocked')

    @record
    def test_get_blob_properties_with_snapshot(self):
        # Arrange
        self._create_container(self.container_name)
        blob_name = 'blob1'
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )
        res = self.bs.snapshot_blob(self.container_name, blob_name)
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 2)

        # Act
        blob = self.bs.get_blob_properties(self.container_name, blob_name, snapshot=res.snapshot)

        # Assert
        self.assertIsNotNone(blob)
        self.assertEqual(blob.properties.blob_type, self.bs.blob_type)
        self.assertEqual(blob.properties.content_length, 11)

    @record
    def test_get_blob_properties_with_leased_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        lease = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        blob = self.bs.get_blob_properties(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.properties.blob_type, self.bs.blob_type)
        self.assertEqual(blob.properties.content_length, 11)
        self.assertEqual(blob.properties.lease.status, 'locked')
        self.assertEqual(blob.properties.lease.state, 'leased')
        self.assertEqual(blob.properties.lease.duration, 'infinite')

    @record
    def test_get_blob_properties_with_non_existing_container(self):
        # Arrange

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_blob_properties(self.container_name, 'blob1')

        # Assert

    @record
    def test_get_blob_properties_with_non_existing_blob(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.get_blob_properties(self.container_name, 'blob1')

        # Assert

    @record
    def test_get_blob_metadata_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        md = self.bs.get_blob_metadata(self.container_name, 'blob1')

        # Assert
        self.assertIsNotNone(md)

    @record
    def test_set_blob_metadata_with_upper_case(self):
        # Arrange
        metadata = {'hello': 'world', 'number': '42', 'UP': 'UPval'}
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        resp = self.bs.set_blob_metadata(self.container_name, 'blob1', metadata)

        # Assert
        self.assertIsNone(resp)
        md = self.bs.get_blob_metadata(self.container_name, 'blob1')
        self.assertEqual(3, len(md))
        self.assertEqual(md['hello'], 'world')
        self.assertEqual(md['number'], '42')
        self.assertEqual(md['up'], 'UPval')

    @record
    def test_delete_blob_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        resp = self.bs.delete_blob(self.container_name, 'blob1')

        # Assert
        self.assertIsNone(resp)

    @record
    def test_delete_blob_with_non_existing_blob(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.delete_blob (self.container_name, 'blob1')

        # Assert

    @record
    def test_delete_blob_snapshot(self):
        # Arrange
        self._create_container(self.container_name)
        blob_name = 'blob1'
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )
        res = self.bs.snapshot_blob(self.container_name, blob_name)
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 2)

        # Act
        self.bs.delete_blob(self.container_name, blob_name, snapshot=res.snapshot)

        # Assert
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0].name, blob_name)
        self.assertIsNone(blobs[0].snapshot)

    @record
    def test_delete_blob_snapshots(self):
        # Arrange
        self._create_container(self.container_name)
        blob_name = 'blob1'
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )
        self.bs.snapshot_blob(self.container_name, blob_name)
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 2)

        # Act
        self.bs.delete_blob(self.container_name, blob_name,
                            delete_snapshots='only')

        # Assert
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 1)
        self.assertIsNone(blobs[0].snapshot)

    @record
    def test_delete_blob_with_snapshots(self):
        # Arrange
        self._create_container(self.container_name)
        blob_name = 'blob1'
        data = b'hello world'
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )
        self.bs.snapshot_blob(self.container_name, blob_name)
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 2)

        # Act
        self.bs.delete_blob(self.container_name, blob_name,
                            delete_snapshots='include')

        # Assert
        blobs = self.bs.list_blobs(self.container_name, include='snapshots')
        self.assertEqual(len(blobs), 0)

    @record
    def test_copy_blob_with_existing_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        sourceblob = '/{0}/{1}/{2}'.format(self.settings.STORAGE_ACCOUNT_NAME,
                                           self.container_name,
                                           'blob1')
        copy = self.bs.copy_blob(self.container_name, 'blob1copy', sourceblob)

        # Assert
        self.assertIsNotNone(copy)
        self.assertEqual(copy.status, 'success')
        self.assertIsNotNone(copy.id)
        copy_blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1copy')
        self.assertEqual(copy_blob.content, b'hello world')

    @record
    def test_copy_blob_async_public_blob(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'12345678' * 1024 * 1024
        source_blob_name = 'sourceblob'
        source_blob_url = self._create_remote_container_and_block_blob(
            source_blob_name, data, 'container')

        # Act
        target_blob_name = 'targetblob'
        copy_resp = self.bs.copy_blob(
            self.container_name, target_blob_name, source_blob_url)

        # Assert
        self.assertEqual(copy_resp.status, 'pending')
        self._wait_for_async_copy(self.container_name, target_blob_name)
        self.assertBlobEqual(self.container_name, target_blob_name, data)

    @record
    def test_copy_blob_async_private_blob(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'12345678' * 1024 * 1024
        source_blob_name = 'sourceblob'
        source_blob_url = self._create_remote_container_and_block_blob(
            source_blob_name, data, None)

        # Act
        target_blob_name = 'targetblob'
        with self.assertRaises(AzureMissingResourceHttpError):
            self.bs.copy_blob(self.container_name,
                              target_blob_name, source_blob_url)

        # Assert

    @record
    def test_copy_blob_async_private_blob_with_sas(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'12345678' * 1024 * 1024
        source_blob_name = 'sourceblob'
        self._create_remote_container_and_block_blob(
            source_blob_name, data, None)

        sas_token = self.bs2.generate_blob_shared_access_signature(
            self.remote_container_name,
            source_blob_name,
            permission=BlobPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),          
        )

        source_blob_url = self.bs2.make_blob_url(
            self.remote_container_name,
            source_blob_name,
            sas_token=sas_token,
        )

        # Act
        target_blob_name = 'targetblob'
        copy_resp = self.bs.copy_blob(
            self.container_name, target_blob_name, source_blob_url)

        # Assert
        self.assertEqual(copy_resp.status, 'pending')
        self._wait_for_async_copy(self.container_name, target_blob_name)
        self.assertBlobEqual(self.container_name, target_blob_name, data)

    @record
    def test_abort_copy_blob(self):
        # Arrange
        self._create_container(self.container_name)
        data = b'12345678' * 1024 * 1024
        source_blob_name = 'sourceblob'
        source_blob_url = self._create_remote_container_and_block_blob(
            source_blob_name, data, 'container')

        # Act
        target_blob_name = 'targetblob'
        copy_resp = self.bs.copy_blob(
            self.container_name, target_blob_name, source_blob_url)
        self.assertEqual(copy_resp.status, 'pending')
        self.bs.abort_copy_blob(
            self.container_name, 'targetblob', copy_resp.id)

        # Assert
        target_blob = self.bs.get_blob_to_bytes(self.container_name, target_blob_name)
        self.assertEqual(target_blob.content, b'')
        self.assertEqual(target_blob.properties.copy.status, 'aborted')

    @record
    def test_abort_copy_blob_with_synchronous_copy_fails(self):
        # Arrange
        source_blob_name = 'sourceblob'
        self._create_container_and_block_blob(
            self.container_name, source_blob_name, b'hello world')
        source_blob_url = self.bs.make_blob_url(
            self.container_name, source_blob_name)

        # Act
        target_blob_name = 'targetblob'
        copy_resp = self.bs.copy_blob(
            self.container_name, target_blob_name, source_blob_url)
        with self.assertRaises(AzureHttpError):
            self.bs.abort_copy_blob(
                self.container_name,
                target_blob_name,
                copy_resp.id)

        # Assert
        self.assertEqual(copy_resp.status, 'success')

    @record
    def test_snapshot_blob(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        resp = self.bs.snapshot_blob(self.container_name, 'blob1')

        # Assert
        self.assertIsNotNone(resp)
        self.assertIsNotNone(resp.snapshot)

    @record
    def test_lease_blob_acquire_and_release(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')
        self.bs.release_blob_lease(self.container_name, 'blob1', lease_id)
        lease_id2 = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Assert
        self.assertIsNotNone(lease_id)
        self.assertIsNotNone(lease_id2)

    @record
    def test_lease_blob_with_duration(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = self.bs.acquire_blob_lease(
            self.container_name, 'blob1', lease_duration=15)
        resp2 = self.bs.create_blob_from_bytes (self.container_name, 'blob1', b'hello 2',
                                 lease_id=lease_id)
        self.sleep(15)

        # Assert
        with self.assertRaises(AzureHttpError):
            self.bs.create_blob_from_bytes (self.container_name, 'blob1', b'hello 3',
                             lease_id=lease_id)

    @record
    def test_lease_blob_with_proposed_lease_id(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = 'a0e6c241-96ea-45a3-a44b-6ae868bc14d0'
        lease_id1 = self.bs.acquire_blob_lease(
            self.container_name, 'blob1',
            proposed_lease_id=lease_id)

        # Assert
        self.assertEqual(lease_id1, lease_id)

    @record
    def test_lease_blob_change_lease_id(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = 'a0e6c241-96ea-45a3-a44b-6ae868bc14d0'
        cur_lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')
        self.bs.change_blob_lease(self.container_name, 'blob1', cur_lease_id, lease_id)
        next_lease_id = self.bs.renew_blob_lease(self.container_name, 'blob1', lease_id)

        # Assert
        self.assertNotEqual(cur_lease_id, next_lease_id)
        self.assertEqual(next_lease_id, lease_id)

    @record
    def test_lease_blob_renew_released_lease_fails(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')
        self.bs.release_blob_lease(self.container_name, 'blob1', lease_id)

        # Assert
        with self.assertRaises(AzureConflictHttpError):
            self.bs.renew_blob_lease(self.container_name, 'blob1', lease_id)

    @record
    def test_lease_blob_break_period(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1',
                                   lease_duration=15)
        lease_time = self.bs.break_blob_lease(self.container_name, 'blob1',
                                   lease_break_period=5)
        blob = self.bs.create_blob_from_bytes (self.container_name, 'blob1', b'hello 2', lease_id=lease_id)
        self.sleep(5)

        with self.assertRaises(AzureHttpError):
            self.bs.create_blob_from_bytes (self.container_name, 'blob1', b'hello 3', lease_id=lease_id)

        # Assert
        self.assertIsNotNone(lease_id)
        self.assertIsNotNone(lease_time)
        self.assertIsNone(blob)

    @record
    def test_lease_blob_break_released_lease_fails(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        lease_id = self.bs.acquire_blob_lease(self.container_name, 'blob1')
        self.bs.release_blob_lease(self.container_name, 'blob1', lease_id)

        # Act
        with self.assertRaises(AzureConflictHttpError):
            self.bs.break_blob_lease(self.container_name, 'blob1')

        # Assert

    @record
    def test_lease_blob_acquire_and_renew(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')

        # Act
        lease_id1 = self.bs.acquire_blob_lease(self.container_name, 'blob1')
        lease_id2 = self.bs.renew_blob_lease(self.container_name, 'blob1', lease_id1)

        # Assert
        self.assertEqual(lease_id1, lease_id2)

    @record
    def test_lease_blob_acquire_twice_fails(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, 'blob1', b'hello world')
        lease_id1 = self.bs.acquire_blob_lease(self.container_name, 'blob1')

        # Act
        with self.assertRaises(AzureHttpError):
            self.bs.acquire_blob_lease(self.container_name, 'blob1')
        self.bs.release_blob_lease(self.container_name, 'blob1', lease_id1)

        # Assert
        self.assertIsNotNone(lease_id1)

    @record
    def test_with_filter(self):
        # Single filter
        if sys.version_info < (3,):
            strtype = (str, unicode)
            strornonetype = (str, unicode, type(None))
        else:
            strtype = str
            strornonetype = (str, type(None))

        called = []

        def my_filter(request, next):
            called.append(True)
            for header in request.headers:
                self.assertIsInstance(header, tuple)
                for item in header:
                    self.assertIsInstance(item, strornonetype)
            self.assertIsInstance(request.host, strtype)
            self.assertIsInstance(request.method, strtype)
            self.assertIsInstance(request.path, strtype)
            self.assertIsInstance(request.query, list)
            self.assertIsInstance(request.body, strtype)
            response = next(request)

            self.assertIsInstance(response.body, (bytes, type(None)))
            self.assertIsInstance(response.headers, list)
            for header in response.headers:
                self.assertIsInstance(header, tuple)
                for item in header:
                    self.assertIsInstance(item, strtype)
            self.assertIsInstance(response.status, int)
            return response

        bc = self.bs.with_filter(my_filter)
        bc.create_container(self.container_name + '0', None, None, False)

        self.assertTrue(called)

        del called[:]

        bc.delete_container(self.container_name + '0')

        self.assertTrue(called)
        del called[:]

        # Chained filters
        def filter_a(request, next):
            called.append('a')
            return next(request)

        def filter_b(request, next):
            called.append('b')
            return next(request)

        bc = self.bs.with_filter(filter_a).with_filter(filter_b)
        bc.create_container(self.container_name + '1', None, None, False)

        self.assertEqual(called, ['b', 'a'])

        bc.delete_container(self.container_name + '1')

        self.assertEqual(called, ['b', 'a', 'b', 'a'])

    @record
    def test_unicode_create_container_unicode_name(self):
        # Arrange
        container_name = self.container_name + u'啊齄丂狛狜'

        # Act
        with self.assertRaises(AzureHttpError):
            # not supported - container name must be alphanumeric, lowercase
            self.bs.create_container(container_name)

        # Assert

    @record
    def test_unicode_get_blob_unicode_name(self):
        # Arrange
        self._create_container_and_block_blob(
            self.container_name, '啊齄丂狛狜', b'hello world')

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, '啊齄丂狛狜')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, b'hello world')

    @record
    def test_create_blob_blob_unicode_data(self):
        # Arrange
        self._create_container(self.container_name)

        # Act
        data = u'hello world啊齄丂狛狜'.encode('utf-8')
        resp = self.bs.create_blob_from_bytes (
            self.container_name, 'blob1', data, )

        # Assert
        self.assertIsNone(resp)

    @record
    def test_unicode_get_blob_unicode_data(self):
        # Arrange
        blob_data = u'hello world啊齄丂狛狜'.encode('utf-8')
        self._create_container_and_block_blob(
            self.container_name, 'blob1', blob_data)

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, blob_data)

    @record
    def test_unicode_get_blob_binary_data(self):
        # Arrange
        base64_data = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+P0BBQkNERUZHSElKS0xNTk9QUVJTVFVWV1hZWltcXV5fYGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6e3x9fn+AgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/wABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj9AQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVpbXF1eX2BhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ent8fX5/gIGCg4SFhoeIiYqLjI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1tre4ubq7vL2+v8DBwsPExcbHyMnKy8zNzs/Q0dLT1NXW19jZ2tvc3d7f4OHi4+Tl5ufo6err7O3u7/Dx8vP09fb3+Pn6+/z9/v8AAQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8wMTIzNDU2Nzg5Ojs8PT4/QEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl9gYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXp7fH1+f4CBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6foKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb3N3e3+Dh4uPk5ebn6Onq6+zt7u/w8fLz9PX29/j5+vv8/f7/AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0+P0BBQkNERUZHSElKS0xNTk9QUVJTVFVWV1hZWltcXV5fYGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6e3x9fn+AgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKztLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLj5OXm5+jp6uvs7e7v8PHy8/T19vf4+fr7/P3+/w=='
        binary_data = base64.b64decode(base64_data)

        self._create_container_and_block_blob(
            self.container_name, 'blob1', binary_data)

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1')

        # Assert
        self.assertIsInstance(blob, Blob)
        self.assertEqual(blob.content, binary_data)

    @record
    def test_no_sas_private_blob(self):
        # Arrange
        data = b'a private blob cannot be read without a shared access signature'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        # Act
        url = self.bs.make_blob_url(self.container_name, blob_name)
        response = requests.get(url)

        # Assert
        self.assertFalse(response.ok)
        self.assertNotEqual(-1, response.text.find('ResourceNotFound'))

    @record
    def test_no_sas_public_blob(self):
        # Arrange
        data = b'a public blob can be read without a shared access signature'
        blob_name = 'blob1.txt'
        self.bs.create_container(self.container_name, None, 'blob')
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )

        # Act
        url = self.bs.make_blob_url(self.container_name, blob_name)
        response = requests.get(url)

        # Assert
        self.assertTrue(response.ok)
        self.assertEqual(data, response.content)

    @record
    def test_public_access_blob(self):
        # Arrange
        data = b'public access blob'
        blob_name = 'blob1.txt'
        self.bs.create_container(self.container_name, None, 'blob')
        self.bs.create_blob_from_bytes (self.container_name, blob_name, data, )

        # Act
        service = BlockBlobService(
            self.settings.STORAGE_ACCOUNT_NAME,
            request_session=requests.Session(),
        )
        self._set_service_options(service, self.settings)
        result = service.get_blob_to_bytes(self.container_name, blob_name)

        # Assert
        self.assertEqual(data, result.content)

    @record
    def test_sas_access_blob(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with read permission on blob'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )
        
        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            permission=BlobPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),
        )

        # Act
        service = BlockBlobService(
            self.settings.STORAGE_ACCOUNT_NAME,
            sas_token=token,
            request_session=requests.Session(),
        )
        self._set_service_options(service, self.settings)
        result = service.get_blob_to_bytes(self.container_name, blob_name)

        # Assert
        self.assertEqual(data, result.content)

    @record
    def test_sas_signed_identifier(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with signed identifier'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        access_policy = AccessPolicy()
        access_policy.start = '2011-10-11'
        access_policy.expiry = '2018-10-12'
        access_policy.permission = BlobPermissions.READ
        identifiers = {'testid': access_policy}

        resp = self.bs.set_container_acl(self.container_name, identifiers)

        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            id='testid'
            )

        # Act
        service = BlockBlobService(
            self.settings.STORAGE_ACCOUNT_NAME,
            sas_token=token,
            request_session=requests.Session(),
        )
        self._set_service_options(service, self.settings)
        result = service.get_blob_to_bytes(self.container_name, blob_name)

        # Assert
        self.assertEqual(data, result.content)

    @record
    def test_account_sas(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with read permission on blob'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_account_shared_access_signature(
            ResourceTypes.OBJECT,
            AccountPermissions.READ,
            datetime.utcnow() + timedelta(hours=1),
        )

        # Act
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )
        response = requests.get(url)

        # Assert
        self.assertTrue(response.ok)
        self.assertEqual(data, response.content)

    @record
    def test_shared_read_access_blob(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with read permission on blob'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            permission=BlobPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),
        )

        # Act
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )
        response = requests.get(url)

        # Assert
        self.assertTrue(response.ok)
        self.assertEqual(data, response.content)

    @record
    def test_shared_read_access_blob_with_content_query_params(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with read permission on blob'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            permission=BlobPermissions.READ,
            expiry=datetime.utcnow() + timedelta(hours=1),
            cache_control='no-cache',
            content_disposition='inline',
            content_encoding='utf-8',
            content_language='fr',
            content_type='text',
        )
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )

        # Act
        response = requests.get(url)

        # Assert
        self.assertEqual(data, response.content)
        self.assertEqual(response.headers['cache-control'], 'no-cache')
        self.assertEqual(response.headers['content-disposition'], 'inline')
        self.assertEqual(response.headers['content-encoding'], 'utf-8')
        self.assertEqual(response.headers['content-language'], 'fr')
        self.assertEqual(response.headers['content-type'], 'text')

    @record
    def test_shared_write_access_blob(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with write permission on blob'
        updated_data = b'updated blob data'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            permission=BlobPermissions.WRITE,
            expiry=datetime.utcnow() + timedelta(hours=1),
        )
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )

        # Act
        headers = {'x-ms-blob-type': self.bs.blob_type}
        response = requests.put(url, headers=headers, data=updated_data)

        # Assert
        self.assertTrue(response.ok)
        blob = self.bs.get_blob_to_bytes(self.container_name, 'blob1.txt')
        self.assertEqual(updated_data, blob.content)

    @record
    def test_shared_delete_access_blob(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with delete permission on blob'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_blob_shared_access_signature(
            self.container_name,
            blob_name,
            permission=BlobPermissions.DELETE,
            expiry=datetime.utcnow() + timedelta(hours=1),
        )
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )

        # Act
        response = requests.delete(url)

        # Assert
        self.assertTrue(response.ok)
        with self.assertRaises(AzureMissingResourceHttpError):
            blob = self.bs.get_blob_to_bytes(self.container_name, blob_name)

    @record
    def test_shared_access_container(self):
        # SAS URL is calculated from storage key, so this test runs live only
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        data = b'shared access signature with read permission on container'
        blob_name = 'blob1.txt'
        self._create_container_and_block_blob(
            self.container_name,
            blob_name,
            data,
        )

        token = self.bs.generate_container_shared_access_signature(
            self.container_name,
            expiry=datetime.utcnow() + timedelta(hours=1),
            permission=ContainerPermissions.READ,
        )
        url = self.bs.make_blob_url(
            self.container_name,
            blob_name,
            sas_token=token,
        )

        # Act
        response = requests.get(url)

        # Assert
        self.assertTrue(response.ok)
        self.assertEqual(data, response.content)

    @record
    def test_get_blob_to_bytes(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        blob = self.bs.get_blob_to_bytes(self.container_name, blob_name)

        # Assert
        self.assertEqual(data, blob.content)

    @record
    def test_get_blob_to_bytes_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_bytes(self.container_name, blob_name)

        # Assert
        self.assertEqual(data, resp.content)

    def test_get_blob_to_bytes_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_bytes(self.container_name, blob_name,
                                         max_connections=10)

        # Assert
        self.assertEqual(data, resp.content)

    @record
    def test_get_blob_to_bytes_with_progress(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_bytes(
            self.container_name, blob_name, progress_callback=callback)

        # Assert
        self.assertEqual(data, resp.content)
        self.assertEqual(progress, self._get_expected_progress(len(data)))

    @record
    def test_get_blob_to_bytes_with_progress_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_bytes(
            self.container_name, blob_name, progress_callback=callback,
            max_connections=2)

        # Assert
        self.assertEqual(data, resp.content)
        self.assertEqual(progress, self._get_expected_progress(len(data), False))

    @record
    def test_get_blob_to_stream(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    @record
    def test_get_blob_to_stream_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    def test_get_blob_to_stream_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream,
                max_connections=10)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    @record
    def test_get_blob_to_stream_non_seekable_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with open(file_path, 'wb') as stream:
            non_seekable_stream = StorageCommonBlobTest.NonSeekableFile(stream)
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, non_seekable_stream,
                max_connections=1)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    def test_get_blob_to_stream_non_seekable_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with open(file_path, 'wb') as stream:
            non_seekable_stream = StorageCommonBlobTest.NonSeekableFile(stream)

            # Parallel downloads require that the file be seekable
            with self.assertRaises(AttributeError):
                resp = self.bs.get_blob_to_stream(
                    self.container_name, blob_name, non_seekable_stream,
                    max_connections=10)

        # Assert

    @record
    def test_get_blob_to_stream_with_progress(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream,
                progress_callback=callback)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)
        self.assertEqual(progress, self._get_expected_progress(len(data)))

    @record
    def test_get_blob_to_stream_with_progress_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream,
                progress_callback=callback, max_connections=2)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)
        self.assertEqual(progress, self._get_expected_progress(len(data), False))

    def test_get_blob_to_stream_with_progress_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        with open(file_path, 'wb') as stream:
            resp = self.bs.get_blob_to_stream(
                self.container_name, blob_name, stream,
                progress_callback=callback,
                max_connections=5)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)
        self.assertEqual(progress, sorted(progress))
        self.assertGreater(len(progress), 0)

    @record
    def test_get_blob_to_path(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    @record
    def test_get_blob_to_path_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    def test_get_blob_to_path_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path,
            max_connections=10)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    def test_ranged_get_blob_to_path_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path, start_range=0,
            max_connections=10)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)

    def test_ranged_get_blob_to_path(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = b'foo'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path, start_range=1, end_range=3,
            range_get_content_md5=True, max_connections=10)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(b"oo", actual)

    def test_ranged_get_blob_to_path_md5_without_end_range_fail(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        data = b'foo'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        with self.assertRaises(ValueError):
            resp = self.bs.get_blob_to_path(
                self.container_name, blob_name, file_path, start_range=1,
                range_get_content_md5=True, max_connections=10)

        # Assert

    @record
    def test_get_blob_to_path_with_progress(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path,
            progress_callback=callback)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)
        self.assertEqual(progress, self._get_expected_progress(len(data)))

    @record
    def test_get_blob_to_path_with_progress_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path,
            progress_callback=callback, max_connections=2)

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(data, actual)
        self.assertEqual(progress, self._get_expected_progress(len(data), False))

    @record
    def test_get_blob_to_path_with_mode(self):
        # Arrange
        blob_name = 'blob1'
        data = b'abcdefghijklmnopqrstuvwxyz'
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)
        with open(file_path, 'wb') as stream:
            stream.write(b'abcdef')

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path, 'a+b')

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(b'abcdef' + data, actual)

    @record
    def test_get_blob_to_path_with_mode_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        data = self._get_oversized_binary_data()
        file_path = 'blob_output.temp.dat'
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)
        with open(file_path, 'wb') as stream:
            stream.write(b'abcdef')

        # Act
        resp = self.bs.get_blob_to_path(
            self.container_name, blob_name, file_path, 'a+b')

        # Assert
        self.assertIsInstance(resp, Blob)
        with open(file_path, 'rb') as stream:
            actual = stream.read()
            self.assertEqual(b'abcdef' + data, actual)

    @record
    def test_get_blob_to_text(self):
        # Arrange
        blob_name = 'blob1'
        text = u'hello 啊齄丂狛狜 world'
        data = text.encode('utf-8')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_text(self.container_name, blob_name)

        # Assert
        self.assertEqual(text, resp.content)

    @record
    def test_get_blob_to_text_with_encoding(self):
        # Arrange
        blob_name = 'blob1'
        text = u'hello 啊齄丂狛狜 world'
        data = text.encode('utf-16')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_text(
            self.container_name, blob_name, 'utf-16')

        # Assert
        self.assertEqual(text, resp.content)

    @record
    def test_get_blob_to_text_chunked_download(self):
        # Arrange
        blob_name = 'blob1'
        text = self._get_oversized_text_data()
        data = text.encode('utf-8')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_text(self.container_name, blob_name)

        # Assert
        self.assertEqual(text, resp.content)

    def test_get_blob_to_text_chunked_download_parallel(self):
        # parallel tests introduce random order of requests, can only run live
        if TestMode.need_recordingfile(self.test_mode):
            return

        # Arrange
        blob_name = 'blob1'
        text = self._get_oversized_text_data()
        data = text.encode('utf-8')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        resp = self.bs.get_blob_to_text(self.container_name, blob_name,
                                        max_connections=10)

        # Assert
        self.assertEqual(text, resp.content)

    @record
    def test_get_blob_to_text_with_progress(self):
        # Arrange
        blob_name = 'blob1'
        text = u'hello 啊齄丂狛狜 world'
        data = text.encode('utf-8')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_text(
            self.container_name, blob_name, progress_callback=callback)

        # Assert
        self.assertEqual(text, resp.content)
        self.assertEqual(progress, self._get_expected_progress(len(data)))

    @record
    def test_get_blob_to_text_with_encoding_and_progress(self):
        # Arrange
        blob_name = 'blob1'
        text = u'hello 啊齄丂狛狜 world'
        data = text.encode('utf-16')
        self._create_container_and_block_blob(
            self.container_name, blob_name, data)

        # Act
        progress = []

        def callback(current, total):
            progress.append((current, total))

        resp = self.bs.get_blob_to_text(
            self.container_name, blob_name, 'utf-16',
            progress_callback=callback)

        # Assert
        self.assertEqual(text, resp.content)
        self.assertEqual(progress, self._get_expected_progress(len(data)))

#------------------------------------------------------------------------------
if __name__ == '__main__':
    unittest.main()
