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

from cinderclient import exceptions as cinder_exception

from ec2api.api import clients
from ec2api.api import common
from ec2api.api import ec2utils
from ec2api.db import api as db_api
from ec2api import exception
from ec2api.i18n import _
import re
import base64
import collections
import fnmatch
import inspect
import operator
from metrics.metric_util import ReportMetrics


"""Snapshot related API implementation
"""


Validator = common.Validator


@ReportMetrics("cinder-client-create-snapshot", True)
def create_snapshot(context, volume_id, description=None, name=None):
    cinder = clients.cinder(context)
    try:
        os_volume = cinder.volumes.get(volume_id)
    except cinder_exception.NotFound:
        raise exception.InvalidVolumeNotFound(id=volume_id)
    # NOTE(ft): Easy fix to allow snapshot creation in statuses other than
    # AVAILABLE without cinder modifications. Potential race condition
    # though. Seems arguably non-fatal.
    if os_volume.status not in ['available', 'in-use',
                                'attaching', 'detaching']:
        msg = (_("'%s' is not in a state where snapshots are allowed.") %
               volume_id)
        raise exception.IncorrectState(reason=msg)
    with common.OnCrashCleaner() as cleaner:
        os_snapshot = cinder.backups.create(
                os_volume.id, description=description, name=name)
        cleaner.addCleanup(os_snapshot.delete)

    return _format_snapshot(context, os_snapshot)

@ReportMetrics("cinder-client-delete-snapshot", True)
def delete_snapshot(context, snapshot_id):
    cinder = clients.cinder(context)
    try:
         os_snapshot=cinder.backups.get(snapshot_id)
         if os_snapshot.status=="deleting" or os_snapshot.status=="deleted" :
               raise exception.InvalidSnapshotNotFound(id=snapshot_id)
    except cinder_exception.NotFound:
        raise exception.InvalidSnapshotNotFound(id=snapshot_id)

    try:
        cinder.backups.delete(snapshot_id)
    except cinder_exception.NotFound:
        raise exception.InvalidSnapshotNotFound(id=snapshot_id)

    # NOTE(andrey-mp) Don't delete item from DB until it disappears from Cloud
    # It will be deleted by describer in the future
    return True


class SnapshotDescriber(object):

    def describe(self, context, ids=None, detail=True, max_results=None, next_token=None):
        self.context = context
        os_items = self.get_os_items(ids, max_results, next_token, detail)
        formatted_items = []
        
	if ((ids is not None) and not isinstance(ids, list)):
            for os_item in os_items:
                   if os_item.status=="deleting" or os_item.status=="deleted" :
                       raise exception.InvalidSnapshotNotFound(id=os_item.id)

        if ((ids is not None) and isinstance(ids, list)):
            self.ids = set(ids or [])
            for os_item in os_items:
                if (os_item.id not in self.ids) : 
                    continue;

                if os_item.status=="deleting" or os_item.status=="deleted" :
                    raise exception.InvalidSnapshotNotFound(id=os_item.id)
                name=os_item.name
                pattern = re.compile("^volume-(.*)backup.base")
                if name is None or pattern.match(name) is None:
                    formatted_item = self.format(os_item, detail)
                    if formatted_item:
                        formatted_items.append(formatted_item)
	    list_count=len(formatted_items)
	    set_count=len(self.ids)
	    if list_count!=set_count :
                    raise exception.InvalidSnapshotNotFound()
        else :
            for os_item in os_items:
                name=os_item.name
                pattern = re.compile("^volume-(.*)backup.base")
                if name is None or pattern.match(name) is None:
                    formatted_item = self.format(os_item, detail)
                    if formatted_item:
                        formatted_items.append(formatted_item)
        return formatted_items

    def format(self, os_snapshot, detail):
        if detail == True :
            return _format_snapshot(self.context, os_snapshot)
        else :
            return _format_snapshot_no_detail(self.context, os_snapshot)

    def get_os_items(self, ids, max_results, next_token, detail):
        if ids is None :
            return clients.cinder(self.context).backups.list(marker=next_token, limit=max_results, detailed=True)
        elif isinstance(ids, list) :
	  count_id=len(ids)
	  if count_id==1:  	
            try:
               return [clients.cinder(self.context).backups.get(ids[0])]
            except cinder_exception.NotFound:
               raise exception.InvalidSnapshotNotFound(id=ids[0])
	  else :
               return clients.cinder(self.context).backups.list(detailed=True)
        else :
          try:
               return [clients.cinder(self.context).backups.get(ids)]
          except cinder_exception.NotFound:
               raise exception.InvalidSnapshotNotFound(id=ids)


def get_paged(self, formatted_items, max_results, next_token):
        SORT_KEY = 'snapshotId'
        if not max_results and not next_token:
            return formatted_items

        formatted_items = sorted(formatted_items,
                                 key=operator.itemgetter(SORT_KEY))
        
        next_item = 0 
        if next_token:
           for i, elem in enumerate(formatted_items):
              if next_token == elem[SORT_KEY]:
                 next_item = i+1
           if next_item == 0 :
              raise exception.InvalidSnapshotNotFound(id=next_token) 
        if next_item:
            formatted_items = formatted_items[next_item:]
        if max_results and max_results < len(formatted_items):
           #next_token = base64.b64encode(str(next_item + max_results))
            formatted_items = formatted_items[:max_results]
        
        return formatted_items


@ReportMetrics("cinder-client-describe-snapshots", True)
def describe_snapshots(context, snapshot_id=None, detail=True,
                       max_results=None, next_token=None):
    if snapshot_id is not None:
        formatted_snapshots = SnapshotDescriber().describe(context, ids=snapshot_id, detail=True)
    else :
        formatted_snapshots = SnapshotDescriber().describe(
           context, detail=detail, max_results=None, next_token=None)
    formatted_snapshots=get_paged(context,formatted_snapshots,max_results,next_token)
    return {'snapshotSet': formatted_snapshots}

def _format_snapshot_no_detail(context, os_snapshot):
    status_map = {'creating': 'pending',
                  'available': 'completed',
                  'deleting': None,
                  'deleted': None,
                  'restoring': 'completed',
                  'error_restoring': 'completed',
                  'error': 'error'}

    mapped_status = status_map.get(os_snapshot.status, os_snapshot.status)
    if not mapped_status or mapped_status is None:
        return None
    
    return {
            #'name': os_snapshot.name,
            'snapshotId': os_snapshot.id,
            'volumeId': os_snapshot.volume_id,
            'status': mapped_status}

def _format_snapshot(context, os_snapshot):
    # NOTE(mikal): this is just a set of strings in cinder. If they
    # implement an enum, then we should move this code to use it. The
    # valid ec2 statuses are "pending", "completed", and "error".
    status_map = {'creating': 'pending',
                  'available': 'completed',
                  'deleting': None,
		  'deleted':None,
                  'restoring': 'completed',
                  'error_restoring': 'completed',
                  'error': 'error'}

    mapped_status = status_map.get(os_snapshot.status, os_snapshot.status)
    if not mapped_status or mapped_status is None:
        return None

    return {
            #'name': os_snapshot.name,
            #'description': os_snapshot.description,
            'snapshotId': os_snapshot.id,
            'volumeId': os_snapshot.volume_id,
            'volumeSize': os_snapshot.size,
            'status': mapped_status,
            'startTime': os_snapshot.created_at}
