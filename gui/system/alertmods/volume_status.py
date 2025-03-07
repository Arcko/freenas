import re
import subprocess

from django.utils.translation import ugettext as _

from freenasUI.freeadmin.hook import HookMetaclass
from freenasUI.storage.models import Volume
from freenasUI.system.alert import alertPlugins, Alert, BaseAlert
from freenasUI.middleware.notifier import notifier


class VolumeStatusAlert(BaseAlert):

    __metaclass__ = HookMetaclass
    __hook_reverse_order__ = False
    name = 'VolumeStatus'

    def on_volume_status_not_healthy(self, vol, status, message):
        if message:
            return Alert(
                Alert.WARN,
                _('The volume %(volume)s status is %(status)s:'
                  ' %(message)s') % {
                    'volume': vol,
                    'status': status,
                    'message': message,
                }
            )
        else:
            return Alert(
                Alert.WARN,
                _('The volume %(volume)s status is %(status)s') % {
                    'volume': vol,
                    'status': status,
                }
            )

    def volumes_status_enabled(self):
        return True

    def on_volume_status_degraded(self, vol, status, message):
        return Alert(
            Alert.CRIT,
            _('The volume %s status is DEGRADED') % vol,
        )

    def run(self):
        if not self.volumes_status_enabled():
            return
        alerts = []
        for vol in Volume.objects.filter(vol_fstype='ZFS'):
            if not vol.is_decrypted():
                continue
            status = vol.status
            message = ""
            if vol.vol_fstype == 'ZFS':
                status, message = notifier().zpool_status(vol.vol_name)

            if status == 'HEALTHY':
                #alerts.append(Alert(
                #    Alert.OK, _('The volume %s status is HEALTHY') % (vol, )
                #))
                pass
            elif status == 'DEGRADED':
                degraded = self.on_volume_status_degraded(vol, status, message)
                if degraded:
                    alerts.append(degraded)
            else:
                alerts.append(
                    self.on_volume_status_not_healthy(vol, status, message)
                )
        return alerts

alertPlugins.register(VolumeStatusAlert)