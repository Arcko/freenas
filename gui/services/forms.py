# Copyright 2010 iXsystems, Inc.
# All rights reserved
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#####################################################################
from collections import OrderedDict
import glob
import logging
import os
import re
import shutil
import subprocess
import sys

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import validate_email
from django.forms import FileField
from django.utils.safestring import mark_safe
from django.utils.translation import (
    ugettext_lazy as _, ungettext_lazy
)

from dojango import forms
from freenasUI import choices
from freenasUI.common import humanize_size
from freenasUI.common.forms import ModelForm, Form
from freenasUI.common.samba import (
    SAMBA_PROVISIONED_FILE,
    Samba4
)
from freenasUI.freeadmin.forms import DirectoryBrowser
from freenasUI.middleware.exceptions import MiddlewareError
from freenasUI.middleware.notifier import notifier
from freenasUI.network.models import Interfaces
from freenasUI.services import models
from freenasUI.services.exceptions import ServiceFailed
from freenasUI.storage.models import Volume, MountPoint, Disk
from freenasUI.storage.widgets import UnixPermissionField
from ipaddr import (
    IPAddress, IPNetwork, AddressValueError, NetmaskValueError,
    IPv4Address, IPv6Address,
)

log = logging.getLogger('services.form')


class servicesForm(ModelForm):
    class Meta:
        fields = '__all__'
        model = models.services

    def save(self, *args, **kwargs):
        obj = super(servicesForm, self).save(*args, **kwargs)
        _notifier = notifier()

        self.enabled_svcs = []
        self.disabled_svcs = []
        if obj.srv_service == 'cifs' and _notifier._started_domaincontroller():
            obj.srv_enable = True
            obj.save()
            started = True
        
        elif obj.srv_service == 'domaincontroller':
            if obj.srv_enable == True:
                if _notifier._started_domaincontroller():
                    started = _notifier.restart("domaincontroller")
                else:
                    started = _notifier.start("domaincontroller")
            else:
                started = _notifier.stop("domaincontroller")

        elif obj.srv_service == 'domaincontroller':
            if obj.srv_enable == True:
                if _notifier._started_domaincontroller():
                    started = _notifier.restart("domaincontroller")
                else:
                    started = _notifier.start("domaincontroller")
            else: 
                started = _notifier.stop("domaincontroller")

        else:
            """
            Using rc.d restart verb and depend on rc_var service_enable
            does not see the best way to handle service start/stop process

            For now lets handle it properly just for ssh and snmp that seems
            to be the most affected for randomly not starting
            """
            if obj.srv_service in ('snmp', 'ssh'):
                if obj.srv_enable:
                    started = _notifier.start(obj.srv_service)
                else:
                    started = _notifier.stop(obj.srv_service)
            else:
                started = _notifier.restart(obj.srv_service)

        self.started = started
        if started is True:
            if not obj.srv_enable:
                obj.srv_enable = True
                obj.save()

        elif started is False:
            if obj.srv_enable:
                obj.srv_enable = False
                obj.save()
                if obj.srv_service == 'ups':
                    _notifier.stop(obj.srv_service)

        return obj

    def done(self, request=None, events=None, **kwargs):
	return super(servicesForm, self).done(request=request, events=events, **kwargs)

class CIFSForm(ModelForm):

    cifs_srv_bindip = forms.MultipleChoiceField(
        label=models.CIFS._meta.get_field('cifs_srv_bindip').verbose_name,
        help_text=models.CIFS._meta.get_field('cifs_srv_bindip').help_text,
        required=False,
        widget=forms.widgets.CheckedMultiSelect(),
    )

    class Meta:
        fields = '__all__'
        exclude = [ 'cifs_SID', 'cifs_srv_bindip' ]
        model = models.CIFS

    def __init__(self, *args, **kwargs): 
        super(CIFSForm, self).__init__(*args, **kwargs)
        if self.data and self.data.get('cifs_srv_bindip'):
            if ',' in self.data['cifs_srv_bindip']:
                self.data = self.data.copy()
                self.data.setlist(
                    'cifs_srv_bindip',
                    self.data['cifs_srv_bindip'].split(',')
                )
        self.fields['cifs_srv_bindip'].choices = list(choices.IPChoices())
        self.fields['cifs_srv_bindip'].initial = (
            self.instance.cifs_srv_bindip.encode('utf-8').split(',')
            if self.instance.id and self.instance.cifs_srv_bindip
            else ''
        )

    def __check_octet(self, v):
        try:
            if v != "" and (int(v, 8) & ~011777):
                raise ValueError
        except:
            raise forms.ValidationError(_("This is not a valid mask"))

    def clean_cifs_srv_workgroup(self):
        netbios = self.cleaned_data.get("cifs_srv_netbiosname")
        workgroup = self.cleaned_data.get("cifs_srv_workgroup").strip()
        if netbios and netbios.lower() == workgroup.lower():
            raise forms.ValidationError("NetBIOS and Workgroup must be unique")
        return workgroup

    def clean_cifs_srv_filemask(self):
        v = self.cleaned_data.get("cifs_srv_filemask").strip()
        self.__check_octet(v)
        return v

    def clean_cifs_srv_dirmask(self):
        v = self.cleaned_data.get("cifs_srv_dirmask").strip()
        self.__check_octet(v)
        return v

    def clean_cifs_srv_bindip(self):
        ips = self.cleaned_data.get("cifs_srv_bindip")
        if not ips:
            return ''
        bind = []
        for ip in ips:
            try:
                IPAddress(ip.encode('utf-8'))
            except:
                raise forms.ValidationError(
                    "This is not a valid IP: %s" % (ip, )
                )
            bind.append(ip)
        return ','.join(bind)

    def save(self):
        obj = super(CIFSForm, self).save(commit=False)
        obj.cifs_srv_bindip = self.cleaned_data.get('cifs_srv_bindip')
        obj.save()

        started = notifier().restart("cifs")
        if (
            started is False
            and
            models.services.objects.get(srv_service='cifs').srv_enable
        ):
            raise ServiceFailed(
                "cifs", _("The CIFS service failed to reload.")
            )


class AFPForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.AFP

    def __init__(self, *args, **kwargs):
        super(AFPForm, self).__init__(*args, **kwargs)
        self.fields['afp_srv_bindip'].choices = list(choices.IPChoices())

    def save(self):
        super(AFPForm, self).save()
        started = notifier().restart("afp")
        if (
            started is False
            and
            models.services.objects.get(srv_service='afp').srv_enable
        ):
            raise ServiceFailed("afp", _("The AFP service failed to reload."))

    def clean_afp_srv_dbpath(self):
        path = self.cleaned_data.get('afp_srv_dbpath')
        if not path:
            return path
        if not os.path.exists(path):
            raise forms.ValidationError(_('This path does not exist.'))
        if not os.path.isdir(path):
            raise forms.ValidationError(_('This path is not a directory.'))
        return path

    def clean(self):
        cleaned_data = self.cleaned_data
        home = cleaned_data['afp_srv_homedir_enable']
        hdir = cleaned_data.get('afp_srv_homedir')
        if hdir and not home:
            self._errors['afp_srv_homedir_enable'] = self.error_class()
            self._errors['afp_srv_homedir_enable'] += self.error_class([
                    _("This field is required for \"Home directories\"."),
            ])
            cleaned_data.pop('afp_srv_homedir_enable', None)
        if home and not hdir:
            self._errors['afp_srv_homedir'] = self.error_class()
            self._errors['afp_srv_homedir'] += self.error_class([
                    _("This field is required for \"Home directories\"."),
            ])
            cleaned_data.pop('afp_srv_homedir', None)
        return cleaned_data


class NFSForm(ModelForm):

    nfs_srv_bindip = forms.MultipleChoiceField(
        label=models.NFS._meta.get_field('nfs_srv_bindip').verbose_name,
        help_text=models.NFS._meta.get_field('nfs_srv_bindip').help_text,
        required=False,
        widget=forms.widgets.CheckedMultiSelect(),
    )

    class Meta:
        model = models.NFS
        exclude = ('nfs_srv_bindip', )
        widgets = {
            'nfs_srv_mountd_port': forms.widgets.TextInput(),
            'nfs_srv_rpcstatd_port': forms.widgets.TextInput(),
            'nfs_srv_rpclockd_port': forms.widgets.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super(NFSForm, self).__init__(*args, **kwargs)
        if self.data and self.data.get('nfs_srv_bindip'):
            if ',' in self.data['nfs_srv_bindip']:
                self.data = self.data.copy()
                self.data.setlist(
                    'nfs_srv_bindip',
                    self.data['nfs_srv_bindip'].split(',')
                )
        self.fields['nfs_srv_bindip'].choices = list(choices.IPChoices())
        self.fields['nfs_srv_bindip'].initial = (
            self.instance.nfs_srv_bindip.encode('utf-8').split(',')
            if self.instance.id and self.instance.nfs_srv_bindip
            else ''
        )
        self.fields.keyOrder.remove('nfs_srv_bindip')
        self.fields.keyOrder.insert(2, 'nfs_srv_bindip')

        self.fields['nfs_srv_mountd_port'].label = (
            self.fields['nfs_srv_mountd_port'].label.lower()
        )
        self.fields['nfs_srv_rpcstatd_port'].label = (
            self.fields['nfs_srv_rpcstatd_port'].label.lower()
        )
        self.fields['nfs_srv_rpclockd_port'].label = (
            self.fields['nfs_srv_rpclockd_port'].label.lower()
        )
        self.instance._original_nfs_srv_v4 = self.instance.nfs_srv_v4

    def clean_nfs_srv_bindip(self):
        ips = self.cleaned_data.get("nfs_srv_bindip")
        if not ips:
            return ''
        bind = []
        for ip in ips:
            try:
                IPAddress(ip.encode('utf-8'))
            except:
                raise forms.ValidationError(
                    "This is not a valid IP: %s" % (ip, )
                )
            bind.append(ip)
        return ','.join(bind)

    def save(self):
        obj = super(NFSForm, self).save(commit=False)
        obj.nfs_srv_bindip = self.cleaned_data.get('nfs_srv_bindip')
        obj.save()
        started = notifier().restart("nfs")
        if (
            started is False
            and
            models.services.objects.get(srv_service='nfs').srv_enable
        ):
            raise ServiceFailed("nfs", _("The NFS service failed to reload."))

    def done(self, *args, **kwargs):
        if self.instance._original_nfs_srv_v4 != self.instance.nfs_srv_v4:
            if self.instance.nfs_srv_v4:
                 notifier().start("nfsv4")
            else:
                 notifier().stop("nfsv4")


class FTPForm(ModelForm):

    ftp_filemask = UnixPermissionField(label=_('File Permission'))
    ftp_dirmask = UnixPermissionField(label=_('Directory Permission'))

    class Meta:
        fields = '__all__'
        model = models.FTP
        widgets = {
            'ftp_port': forms.widgets.TextInput(),
            'ftp_passiveportsmin': forms.widgets.TextInput(),
            'ftp_passiveportsmax': forms.widgets.TextInput(),
        }

    def __init__(self, *args, **kwargs):

        if 'instance' in kwargs:
            instance = kwargs['instance']
            try:
                mask = int(instance.ftp_filemask, 8)
                instance.ftp_filemask = "%.3o" % (~mask & 0o666)
            except ValueError:
                pass

            try:
                mask = int(instance.ftp_dirmask, 8)
                instance.ftp_dirmask = "%.3o" % (~mask & 0o777)
            except ValueError:
                pass

        super(FTPForm, self).__init__(*args, **kwargs)
        self.instance._original_ftp_tls = self.instance.ftp_tls

    def clean_ftp_passiveportsmin(self):
        ports = self.cleaned_data['ftp_passiveportsmin']
        if (ports < 1024 or ports > 65535) and ports != 0:
            raise forms.ValidationError(
                _("This value must be between 1024 and 65535, inclusive. 0 "
                    "for default")
            )
        return ports

    def clean_ftp_passiveportsmax(self):
        _min = self.cleaned_data['ftp_passiveportsmin']
        ports = self.cleaned_data['ftp_passiveportsmax']
        if (ports < 1024 or ports > 65535) and ports != 0:
            raise forms.ValidationError(
                _("This value must be between 1024 and 65535, inclusive. 0 "
                    "for default.")
            )
        if _min >= ports and ports != 0:
            raise forms.ValidationError(
                _("This must be higher than minimum passive port")
            )
        return ports

    def clean_ftp_filemask(self):
        perm = self.cleaned_data['ftp_filemask']
        perm = int(perm, 8)
        mask = (~perm & 0o666)
        return "%.3o" % mask

    def clean_ftp_dirmask(self):
        perm = self.cleaned_data['ftp_dirmask']
        perm = int(perm, 8)
        mask = (~perm & 0o777)
        return "%.3o" % mask

    def clean_ftp_anonpath(self):
        anon = self.cleaned_data['ftp_onlyanonymous']
        path = self.cleaned_data['ftp_anonpath']
        if anon and not path:
            raise forms.ValidationError(
                _("This field is required for anonymous login")
            )
        return path

    def clean(self):
        cdata = self.cleaned_data
        ftp_tls = cdata.get("ftp_tls")
        if not ftp_tls: 
            return cdata

        certificate = cdata["ftp_ssltls_certificate"]
        if not certificate:
            raise forms.ValidationError(
                "TLS specified without certificate")

        return cdata

    def save(self):
        super(FTPForm, self).save()
        started = notifier().reload("ftp")
        if (
            started is False
            and
            models.services.objects.get(srv_service='ftp').srv_enable
        ):
            raise ServiceFailed("ftp", _("The ftp service failed to start."))

    def done(self, *args, **kwargs):
        if (
            self.instance._original_ftp_tls != self.instance.ftp_tls
            and
            not self.instance._original_ftp_tls
        ) or (self.instance.ftp_tls and not self.instance.ftp_ssltls_certfile):
            notifier().start_ssl("proftpd")


class TFTPForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.TFTP
        widgets = {
            'tftp_port': forms.widgets.TextInput(),
        }

    def save(self):
        super(TFTPForm, self).save()
        started = notifier().reload("tftp")
        if (
            started is False
            and
            models.services.objects.get(srv_service='tftp').srv_enable
        ):
            raise ServiceFailed(
                "tftp", _("The tftp service failed to reload.")
            )


class SSHForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.SSH
        widgets = {
            'ssh_tcpport': forms.widgets.TextInput(),
        }

    def save(self):
        super(SSHForm, self).save()
        started = notifier().reload("ssh")
        if (
            started is False
            and
            models.services.objects.get(srv_service='ssh').srv_enable
        ):
            raise ServiceFailed("ssh", _("The SSH service failed to reload."))


class RsyncdForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.Rsyncd

    def save(self):
        super(RsyncdForm, self).save()
        started = notifier().reload("rsync")
        if (
            started is False
            and
            models.services.objects.get(srv_service='rsync').srv_enable
        ):
            raise ServiceFailed(
                "rsync", _("The Rsync service failed to reload.")
            )


class RsyncModForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.RsyncMod

    def clean_rsyncmod_name(self):
        name = self.cleaned_data['rsyncmod_name']
        if re.search(r'[/\]]', name):
            raise forms.ValidationError(
                _(u"The name cannot contain slash or a closing square backet.")
            )
        name = name.strip()
        return name

    def clean_rsyncmod_hostsallow(self):
        hosts = self.cleaned_data['rsyncmod_hostsallow']
        hosts = hosts.replace("\n", " ").strip()
        return hosts

    def clean_rsyncmod_hostsdeny(self):
        hosts = self.cleaned_data['rsyncmod_hostsdeny']
        hosts = hosts.replace("\n", " ").strip()
        return hosts

    def save(self):
        super(RsyncModForm, self).save()
        started = notifier().reload("rsync")
        if (
            started is False
            and
            models.services.objects.get(srv_service='rsync').srv_enable
        ):
            raise ServiceFailed(
                "rsync", _("The Rsync service failed to reload.")
            )


class DynamicDNSForm(ModelForm):
    ddns_password2 = forms.CharField(
        max_length=50,
        label=_("Confirm Password"),
        widget=forms.widgets.PasswordInput(),
        required=False,
    )

    class Meta:
        model = models.DynamicDNS
        widgets = {
            'ddns_password': forms.widgets.PasswordInput(render_value=False),
        }
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super(DynamicDNSForm, self).__init__(*args, **kwargs)
        if self.instance.ddns_password:
            self.fields['ddns_password'].required = False
        if self._api is True:
            del self.fields['ddns_password2']

    def clean_ddns_password2(self):
        password1 = self.cleaned_data.get("ddns_password")
        password2 = self.cleaned_data.get("ddns_password2")
        if password1 != password2:
            raise forms.ValidationError(
                _("The two password fields didn't match.")
            )
        return password2

    def clean(self):
        cdata = self.cleaned_data
        if not cdata.get("ddns_password"):
            cdata['ddns_password'] = self.instance.ddns_password
        return cdata

    def save(self):
        obj = super(DynamicDNSForm, self).save(commit=False)
        obj.ddns_password = notifier().pwenc_encrypt(
            self.cleaned_data.get('ddns_password')
        )
        obj.save()
        started = notifier().restart("dynamicdns")
        if (
            started is False
            and
            models.services.objects.get(srv_service='dynamicdns').srv_enable
        ):
            raise ServiceFailed(
                "dynamicdns", _("The DynamicDNS service failed to reload.")
            )
DynamicDNSForm.base_fields.keyOrder.remove('ddns_password2')
DynamicDNSForm.base_fields.keyOrder.insert(5, 'ddns_password2')


class SNMPForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.SNMP

    def clean_snmp_contact(self):
        contact = self.cleaned_data['snmp_contact']
        if '@' in contact:
            validate_email(contact)
        elif not re.match(r'^[-_a-zA-Z0-9\s]+$', contact):
            raise forms.ValidationError(
                _(u"The contact must contain only alphanumeric characters, _, "
                    "- or a valid e-mail address")
            )
        return contact

    def clean_snmp_comunity(self):
        community = self.cleaned_data['snmp_community']
        if not re.match(r'^[-_a-zA-Z0-9\s]+$', community):
            raise forms.ValidationError(
                _(u"The community must contain only alphanumeric characters, "
                    "_ or -")
            )
        return community

    def save(self):
        super(SNMPForm, self).save()
        started = notifier().restart("snmp")
        if (
            started is False
            and
            models.services.objects.get(srv_service='snmp').srv_enable
        ):
            raise ServiceFailed(
                "snmp", _("The SNMP service failed to reload.")
            )


class UPSForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.UPS
        widgets = {
            'ups_remoteport': forms.widgets.TextInput(),
            'ups_driver': forms.widgets.FilteringSelect(),
        }

    def __init__(self, *args, **kwargs):
        super(UPSForm, self).__init__(*args, **kwargs)
        self.fields['ups_shutdown'].widget.attrs['onChange'] = mark_safe(
            "disableGeneric('id_ups_shutdown', ['id_ups_shutdowntimer'], "
            "function(box) { if(box.get('value') == 'lowbatt') { return true; "
            "} else { return false; } });")
        self.fields['ups_mode'].widget.attrs['onChange'] = "upsModeToggle();"
        if self.instance.id and self.instance.ups_shutdown == 'lowbatt':
            self.fields['ups_shutdowntimer'].widget.attrs['class'] = (
                'dijitDisabled dijitTextBoxDisabled '
                'dijitValidationTextBoxDisabled')
        ports = filter(lambda x: x.find('.') == -1, glob.glob('/dev/cua*'))
        ports.extend(glob.glob('/dev/ugen*'))
        self.fields['ups_port'] = forms.ChoiceField(
            label=_("Port"),
            required=False,
        )
        self.fields['ups_port'].widget = forms.widgets.ComboBox()
        self.fields['ups_port'].choices = [(port, port) for port in ports]
        if self.data and self.data.get("ups_port"):
            self.fields['ups_port'].choices.insert(
                0, (self.data.get("ups_port"), self.data.get("ups_port"))
            )
        elif self.instance.id:
            self.fields['ups_port'].choices.insert(
                0, (self.instance.ups_port, self.instance.ups_port)
            )

    def clean_ups_port(self):
        port = self.cleaned_data.get("ups_port")
        if self.cleaned_data.get("ups_mode") == 'master' and not port:
            raise forms.ValidationError(
                _("This field is required")
            )
        return port

    def clean_ups_remotehost(self):
        rhost = self.cleaned_data.get("ups_remotehost")
        if self.cleaned_data.get("ups_mode") != 'master':
            if not rhost:
                raise forms.ValidationError(
                    _("This field is required")
                )
        return rhost

    def clean_ups_identifier(self):
        ident = self.cleaned_data.get("ups_identifier")
        if not re.search(r'^[a-z0-9\.\-_]+$', ident, re.I):
            raise forms.ValidationError(
                _("Use alphanumeric characters, \".\", \"-\" and \"_\".")
            )
        return ident

    def clean_ups_monuser(self):
        user = self.cleaned_data.get("ups_monuser")
        if re.search(r'[ #]', user, re.I):
            raise forms.ValidationError(
                _("Spaces or number signs are not allowed.")
            )
        return user

    def clean_ups_monpwd(self):
        pwd = self.cleaned_data.get("ups_monpwd")
        if re.search(r'[ #]', pwd, re.I):
            raise forms.ValidationError(
                _("Spaces or number signs are not allowed.")
            )
        return pwd

    def clean_ups_toemail(self):
        email = self.cleaned_data.get("ups_toemail")
        if email:
            invalids = []
            for e in email.split(';'):
                try:
                    validate_email(e.strip())
                except:
                    invalids.append(e.strip())

            if len(invalids) > 0:
                raise forms.ValidationError(ungettext_lazy(
                    'The email %(email)s is not valid',
                    'The following emails are not valid: %(email)s',
                    len(invalids)) % {
                        'email': ", ".join(invalids),
                    })
        return email

    def save(self):
        obj = super(UPSForm, self).save(commit=False)
        obj.ups_monpwd = notifier().pwenc_encrypt(
            self.cleaned_data.get('ups_monpwd')
        )
        obj.save()
        started = notifier().restart("ups")
        if (
            started is False
            and
            models.services.objects.get(srv_service='ups').srv_enable
        ):
            raise ServiceFailed("ups", _("The UPS service failed to reload."))


class LLDPForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.LLDP

    def save(self):
        super(LLDPForm, self).save()
        started = notifier().restart("lldp")


class iSCSITargetAuthCredentialForm(ModelForm):
    iscsi_target_auth_secret2 = forms.CharField(
        label=_("Secret (Confirm)"),
        widget=forms.PasswordInput(render_value=True),
        help_text=_("Enter the same secret above for verification.")
    )
    iscsi_target_auth_peersecret2 = forms.CharField(
        label=_("Peer Secret (Confirm)"),
        widget=forms.PasswordInput(render_value=True),
        help_text=_("Enter the same secret above for verification."),
        required=False,
    )

    class Meta:
        fields = '__all__'
        model = models.iSCSITargetAuthCredential
        widgets = {
            'iscsi_target_auth_secret': forms.PasswordInput(render_value=True),
            'iscsi_target_auth_peersecret': forms.PasswordInput(
                render_value=True
            ),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetAuthCredentialForm, self).__init__(*args, **kwargs)
        if self._api:
            del self.fields['iscsi_target_auth_secret2']
            del self.fields['iscsi_target_auth_peersecret2']
        else:
            self.fields.keyOrder = [
                'iscsi_target_auth_tag',
                'iscsi_target_auth_user',
                'iscsi_target_auth_secret',
                'iscsi_target_auth_secret2',
                'iscsi_target_auth_peeruser',
                'iscsi_target_auth_peersecret',
                'iscsi_target_auth_peersecret2'
            ]

            ins = kwargs.get("instance", None)
            if ins:
                self.fields['iscsi_target_auth_secret2'].initial = (
                    self.instance.iscsi_target_auth_secret)
                self.fields['iscsi_target_auth_peersecret2'].initial = (
                    self.instance.iscsi_target_auth_peersecret)

    def _clean_secret_common(self, secretprefix):
        secret1 = self.cleaned_data.get(secretprefix, "")
        secret2 = self.cleaned_data[("%s2" % secretprefix)]
        if secret1 != secret2:
            raise forms.ValidationError(_("Secret does not match"))
        return secret2

    def clean_iscsi_target_auth_secret2(self):
        return self._clean_secret_common("iscsi_target_auth_secret")

    def clean_iscsi_target_auth_peersecret2(self):
        return self._clean_secret_common("iscsi_target_auth_peersecret")

    def clean(self):
        cdata = self.cleaned_data

        if len(cdata.get('iscsi_target_auth_peeruser', '')) > 0:
            if len(cdata.get('iscsi_target_auth_peersecret', '')) == 0:
                cdata.pop('iscsi_target_auth_peersecret', None)
                self._errors['iscsi_target_auth_peersecret'] = (
                    self.error_class([_(
                        "The peer secret is required if you set a peer user."
                    )])
                )
                self._errors['iscsi_target_auth_peersecret2'] = (
                    self.error_class([_(
                        "The peer secret is required if you set a peer user."
                    )])
                )
            elif cdata.get('iscsi_target_auth_peersecret', '') == cdata.get(
                'iscsi_target_auth_secret', ''
            ):
                del cdata['iscsi_target_auth_peersecret']
                self._errors['iscsi_target_auth_peersecret'] = (
                    self.error_class([_(
                        "The peer secret cannot be the same as user secret."
                    )])
                )
        else:
            if len(cdata.get('iscsi_target_auth_peersecret', '')) > 0:
                self._errors['iscsi_target_auth_peersecret'] = (
                    self.error_class([_(
                        "The peer user is required if you set a peer secret."
                    )])
                )
                del cdata['iscsi_target_auth_peersecret']
            if len(cdata.get('iscsi_target_auth_peersecret2', '')) > 0:
                self._errors['iscsi_target_auth_peersecret2'] = (
                    self.error_class([_(
                        "The peer user is required if you set a peer secret."
                    )])
                )
                del cdata['iscsi_target_auth_peersecret2']

        return cdata

    def save(self, commit=True):
        obj = super(iSCSITargetAuthCredentialForm, self).save(commit=False)
        obj.iscsi_target_auth_secret = self.cleaned_data.get(
            'iscsi_target_auth_secret'
        )
        obj.iscsi_target_auth_peersecret = self.cleaned_data.get(
            'iscsi_target_auth_peersecret'
        )
        if commit:
            obj.save()
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(
            srv_service='iscsitarget'
        ).srv_enable:
            raise ServiceFailed(
                "iscsitarget", _("The iSCSI service failed to reload.")
            )
        return obj


class iSCSITargetToExtentForm(ModelForm):
    class Meta:
        fields = '__all__'
        model = models.iSCSITargetToExtent
        widgets = {
            'iscsi_target': forms.widgets.FilteringSelect(),
            'iscsi_extent': forms.widgets.FilteringSelect(),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetToExtentForm, self).__init__(*args, **kwargs)
        qs = models.iSCSITargetToExtent.objects.all()
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        used = [o.iscsi_lunid for o in qs]
        total = len(used) + 15
        choices = tuple(
            [('', 'Auto')] + [(x, x) for x in xrange(total) if x not in used]
        )
        self.fields['iscsi_lunid'] = forms.ChoiceField(
            label=self.fields['iscsi_lunid'].label,
            initial=self.fields['iscsi_lunid'].initial,
            choices=choices,
            required=False,
        )
        qs = self.fields['iscsi_extent'].queryset
        exc = models.iSCSITargetToExtent.objects.all()
        if self.instance:
            exc = exc.exclude(id=self.instance.id)
        self.fields['iscsi_extent'].queryset = qs.exclude(id__in=[e.iscsi_extent.id for e in exc])

    def clean_iscsi_lunid(self):
        lunid = self.cleaned_data.get('iscsi_lunid')
        if not lunid:
            return None
        qs = models.iSCSITargetToExtent.objects.filter(iscsi_lunid=lunid)
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise forms.ValidationError(_("LUN ID already exists."))
        return lunid

    def save(self):
        super(iSCSITargetToExtentForm, self).save()
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget", _("The iSCSI service failed to reload."))


class iSCSITargetGlobalConfigurationForm(ModelForm):
    iscsi_discoveryauthgroup = forms.ChoiceField(label=_("Discovery Auth Group"))

    class Meta:
        fields = '__all__'
        model = models.iSCSITargetGlobalConfiguration
        widgets = {
            'iscsi_pool_avail_threshold': forms.widgets.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetGlobalConfigurationForm, self).__init__(*args, **kwargs)
        self.fields['iscsi_discoveryauthgroup'].required = False
        self.fields['iscsi_discoveryauthgroup'].choices = [('-1', _('None'))] + [(i['iscsi_target_auth_tag'], i['iscsi_target_auth_tag']) for i in models.iSCSITargetAuthCredential.objects.all().values('iscsi_target_auth_tag').distinct()]

    def _clean_number_range(self, field, start, end):
        f = self.cleaned_data[field]
        if f < start or f > end:
            raise forms.ValidationError(
                _("This value must be between %(start)d and %(end)d, "
                    "inclusive.") % {
                        'start': start,
                        'end': end,
                    }
            )
        return f

    def clean_iscsi_discoveryauthgroup(self):
        discoverymethod = self.cleaned_data['iscsi_discoveryauthmethod']
        discoverygroup = self.cleaned_data['iscsi_discoveryauthgroup']
        if discoverygroup in ('', None):
            return None
        if discoverymethod in ('CHAP', 'CHAP Mutual'):
            if int(discoverygroup) == -1:
                raise forms.ValidationError(_("This field is required if discovery method is set to CHAP or CHAP Mutual."))
        elif int(discoverygroup) == -1:
            return None
        return discoverygroup

    def clean_iscsi_isns_servers(self):
        servers = self.cleaned_data.get('iscsi_isns_servers')
        servers = servers.replace('\n', ' ').strip(' ')
        servers = re.sub(r'\s+', ' ', servers)
        errors = []
        for server in servers.split(' '):
            if not server:
                continue
            reg = re.search(r'\[(.+?)\](:[0-9]+)?', server)
            if reg:
                ip = reg.group(1)
                try:
                    IPv6Address(ip)
                    continue
                except AddressValueError:
                    errors.append(ip)
            reg = re.search(
                r'([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})(:[0-9]+)?',
                server,
            )
            if reg:
                ip = reg.group(1)
                try:
                    IPv4Address(ip)
                    continue
                except AddressValueError:
                    errors.append(ip)
            errors.append(server)
                
        if errors:
            raise forms.ValidationError(_(
                'The following addresses are not valid: %s') % ', '.join(errors)
            )
            
        return servers

    def save(self):
        obj = super(iSCSITargetGlobalConfigurationForm, self).save()
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget", _("The iSCSI service failed to reload."))


class iSCSITargetExtentForm(ModelForm):

    iscsi_target_extent_type = forms.ChoiceField(
        choices=(
            ('File', _('File')),
            ('Disk', _('Device')),
        ),
        label=_("Extent Type"),
    )
    iscsi_target_extent_disk = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={'maxHeight': 200}),
        label=_('Device'),
        required=False,
    )

    class Meta:
        model = models.iSCSITargetExtent
        exclude = (
            'iscsi_target_extent_type',
        )
        widgets = {
            'iscsi_target_extent_path': DirectoryBrowser(dirsonly=False),
            'iscsi_target_extent_avail_threshold': forms.widgets.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetExtentForm, self).__init__(*args, **kwargs)
        self.fields.keyOrder.remove('iscsi_target_extent_type')
        self.fields.keyOrder.insert(1, 'iscsi_target_extent_type')
        if self.instance.id:
            if self.instance.iscsi_target_extent_type == 'File':
                self.fields['iscsi_target_extent_type'].initial = 'File'
            else:
                self.fields['iscsi_target_extent_type'].initial = 'Disk'
            self.fields['iscsi_target_extent_disk'].choices = self._populate_disk_choices(exclude=self.instance)
            if self.instance.iscsi_target_extent_type == 'ZVOL':
                self.fields['iscsi_target_extent_disk'].initial = self.instance.iscsi_target_extent_path
            else:
                self.fields['iscsi_target_extent_disk'].initial = self.instance.get_device()[5:]
            self._path = self.instance.iscsi_target_extent_path
            self._name = self.instance.iscsi_target_extent_name
        else:
            self.fields['iscsi_target_extent_disk'].choices = self._populate_disk_choices()
        self.fields['iscsi_target_extent_type'].widget.attrs['onChange'] = "iscsiExtentToggle();extentZvolToggle();"
        self.fields['iscsi_target_extent_path'].required = False

        self.fields['iscsi_target_extent_disk'].widget.attrs['onChange'] = (
            'extentZvolToggle();'
        )

    def _populate_disk_choices(self, exclude=None):

        diskchoices = OrderedDict()

        qs = models.iSCSITargetExtent.objects.filter(iscsi_target_extent_type='Disk')
        if exclude:
            qs = qs.exclude(id=exclude.id)
        diskids = [i[0] for i in qs.values_list('iscsi_target_extent_path')]
        used_disks = [d.disk_name for d in Disk.objects.filter(id__in=diskids)]

        qs = models.iSCSITargetExtent.objects.filter(iscsi_target_extent_type='ZVOL')
        if exclude:
            qs = qs.exclude(id=exclude.id)
        used_zvol = [i[0] for i in qs.values_list('iscsi_target_extent_path')]

        for v in Volume.objects.all():
            used_disks.extend(v.get_disks())

        _notifier = notifier()
        zsnapshots = _notifier.zfs_snapshot_list(sort='name')
        snaps = []
        for volume in Volume.objects.filter(vol_fstype__exact='ZFS'):
            zvols = _notifier.list_zfs_vols(volume.vol_name, sort='name')
            for zvol, attrs in zvols.items():
                if "zvol/" + zvol not in used_zvol:
                    diskchoices["zvol/" + zvol] = "%s (%s)" % (
                        zvol,
                        humanize_size(attrs['volsize']))
                if zvol not in zsnapshots:
                    continue
                snaps.extend(zsnapshots.get(zvol))
        for snap in snaps:
            diskchoices["zvol/" + snap.fullname] = "%s (%s) [ro]" % (
                snap.fullname,
                humanize_size(attrs['volsize']))

        # Grab partition list
        # NOTE: This approach may fail if device nodes are not accessible.
        disks = _notifier.get_disks()
        for name, disk in disks.items():
            if name in used_disks:
                continue
            capacity = humanize_size(disk['capacity'])
            diskchoices[name] = "%s (%s)" % (name, capacity)

        # HAST Devices through GEOM GATE
        gate_pipe = subprocess.Popen(
            """/usr/sbin/diskinfo `/sbin/geom gate status -s"""
            """| /usr/bin/cut -d" " -f1` | /usr/bin/cut -f1,3""",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        gate_diskinfo = gate_pipe.communicate()[0].strip().split('\n')
        for disk in gate_diskinfo:
            if disk:
                devname, capacity = disk.split('\t')
                capacity = humanize_size(capacity)
                diskchoices[devname] = "%s (%s)" % (devname, capacity)
        return diskchoices.items()

    def clean_iscsi_target_extent_name(self):
        name = self.cleaned_data.get('iscsi_target_extent_name')
        if not name:
            return name
        qs = models.iSCSITargetExtent.objects.filter(
            iscsi_target_extent_name=name
        )
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise forms.ValidationError(_('Extent name must be unique.'))
        return name

    def clean_iscsi_target_extent_disk(self):
        _type = self.cleaned_data.get('iscsi_target_extent_type')
        disk = self.cleaned_data.get('iscsi_target_extent_disk')
        if _type == 'disk' and not disk:
            raise forms.ValidationError(
                _("This field is required")
            )
        return disk

    def clean_iscsi_target_extent_path(self):
        _type = self.cleaned_data['iscsi_target_extent_type']
        if _type == 'Disk':
            return ''
        path = self.cleaned_data["iscsi_target_extent_path"]
        if not path:
            return None
        if (os.path.exists(path) and not os.path.isfile(path)) or path[-1] == '/':
            raise forms.ValidationError(_("You need to specify a filepath, not a directory."))
        valid = False
        for mp in MountPoint.objects.all():
            if path == mp.mp_path:
                raise forms.ValidationError(
                    _("You need to specify a file inside your volume/dataset.")
                )
            if path.startswith(mp.mp_path + '/'):
                valid = True
        if not valid:
            raise forms.ValidationError(_("Your path to the extent must reside inside a volume/dataset mount point."))
        return path

    def clean_iscsi_target_extent_filesize(self):
        size = self.cleaned_data['iscsi_target_extent_filesize']
        try:
            int(size)
        except ValueError:
            suffixes = ['KB', 'MB', 'GB', 'TB']
            for x in suffixes:
                if size.upper().endswith(x):
                    m = re.match(r'(\d+)\s*?(%s)' % x, size)
                    if m:
                        return "%s%s" % (m.group(1), m.group(2))
            raise forms.ValidationError(_("This value can be a size in bytes, or can be postfixed with KB, MB, GB, TB"))
        return size

    def clean(self):
        cdata = self.cleaned_data
        _type = cdata.get('iscsi_target_extent_type')
        path = cdata.get("iscsi_target_extent_path")
        if (
            cdata.get("iscsi_target_extent_filesize") == "0"
            and
            path
            and
            (
                not os.path.exists(path)
                or
                (
                    os.path.exists(path)
                    and
                    not os.path.isfile(path)
                )
            )
        ):
            self._errors['iscsi_target_extent_path'] = self.error_class([
                _("The file must exist if the extent size is set to auto (0)")
            ])
            del cdata['iscsi_target_extent_path']
        elif _type == 'file' and not path:
            self._errors['iscsi_target_extent_path'] = self.error_class([
                _("This field is required")
            ])
        return cdata

    def save(self, commit=True):
        oExtent = super(iSCSITargetExtentForm, self).save(commit=False)
        if commit and self.cleaned_data["iscsi_target_extent_type"] == 'Disk':
            if self.cleaned_data["iscsi_target_extent_disk"].startswith("zvol"):
                oExtent.iscsi_target_extent_path = self.cleaned_data["iscsi_target_extent_disk"]
                oExtent.iscsi_target_extent_type = 'ZVOL'
            elif self.cleaned_data["iscsi_target_extent_disk"].startswith("multipath"):
                notifier().unlabel_disk(str(self.cleaned_data["iscsi_target_extent_disk"]))
                notifier().label_disk("extent_%s" % self.cleaned_data["iscsi_target_extent_disk"], self.cleaned_data["iscsi_target_extent_disk"])
                mp_name = self.cleaned_data["iscsi_target_extent_disk"].split("/")[-1]
                diskobj = models.Disk.objects.get(disk_multipath_name=mp_name)
                oExtent.iscsi_target_extent_type = 'Disk'
                oExtent.iscsi_target_extent_path = str(diskobj.id)
            else:
                diskobj = models.Disk.objects.filter(
                    disk_name=self.cleaned_data["iscsi_target_extent_disk"],
                    disk_enabled=True,
                ).order_by('-id')[0]
                # label it only if it is a real disk
                if (
                    diskobj.disk_identifier.startswith("{devicename}")
                    or
                    diskobj.disk_identifier.startswith("{uuid}")
                ):
                    success, msg = notifier().label_disk(
                        "extent_%s" % self.cleaned_data["iscsi_target_extent_disk"],
                        self.cleaned_data["iscsi_target_extent_disk"]
                    )
                    if success is False:
                        raise MiddlewareError(_(
                            "Serial not found and glabel failed for "
                            "%(disk)s: %(error)s" % {
                                'disk': self.cleaned_data["iscsi_target_extent_disk"],
                                'error': msg,
                            })
                        )
                    notifier().sync_disk(self.cleaned_data["iscsi_target_extent_disk"])
                oExtent.iscsi_target_extent_type = 'Disk'
                oExtent.iscsi_target_extent_path = str(diskobj.id)
            oExtent.iscsi_target_extent_filesize = 0
            oExtent.save()

        elif commit and self.cleaned_data['iscsi_target_extent_type'] == 'File':
            oExtent.iscsi_target_extent_type = 'File'
            oExtent.save()

            path = self.cleaned_data["iscsi_target_extent_path"]
            dirs = "/".join(path.split("/")[:-1])
            if not os.path.exists(dirs):
                try:
                    os.makedirs(dirs)
                except Exception, e:
                    log.error("Unable to create dirs for extent file: %s", e)
            if not os.path.exists(path):
                size = self.cleaned_data["iscsi_target_extent_filesize"]
                if size.lower().endswith("b"):
                    size = size[:-1]
                os.system("truncate -s %s %s" % (size, path))

        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget", _("The iSCSI service failed to reload."))
        return oExtent
iSCSITargetExtentForm.base_fields.keyOrder.remove('iscsi_target_extent_disk')
iSCSITargetExtentForm.base_fields.keyOrder.insert(2, 'iscsi_target_extent_disk')


class iSCSITargetPortalForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.iSCSITargetPortal
        widgets = {
            'iscsi_target_portal_tag': forms.widgets.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetPortalForm, self).__init__(*args, **kwargs)
        self.fields["iscsi_target_portal_tag"].initial = (
            models.iSCSITargetPortal.objects.all().count() + 1)

    def clean_iscsi_target_portal_tag(self):
        tag = self.cleaned_data["iscsi_target_portal_tag"]
        higher = models.iSCSITargetPortal.objects.all().count() + 1
        if tag > higher:
            raise forms.ValidationError(_("Your Portal Group ID cannot be higher than %d") % higher)
        return tag

    def done(self, *args, **kwargs):
        super(iSCSITargetPortalForm, self).done(*args, **kwargs)
        # This must be done here and not on save() because it saves foreign keys
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget", _("The iSCSI service failed to reload."))


class iSCSITargetPortalIPForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.iSCSITargetPortalIP
        widgets = {
            'iscsi_target_portalip_port': forms.widgets.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super(iSCSITargetPortalIPForm, self).__init__(*args, **kwargs)
        self.fields['iscsi_target_portalip_ip'] = forms.ChoiceField(
            label=self.fields['iscsi_target_portalip_ip'].label,
        )
        ips = [('', '------'), ('0.0.0.0', '0.0.0.0')]
        for interface in Interfaces.objects.all():
            if interface.int_ipv4address:
                ips.append((interface.int_ipv4address, interface.int_ipv4address))
            elif interface.int_ipv6address:
                ips.append((interface.int_ipv6address, interface.int_ipv6address))
            for alias in interface.alias_set.all():
                if alias.alias_v4address:
                    ips.append((alias.alias_v4address, alias.alias_v4address))
                elif alias.alias_v6address:
                    ips.append((alias.alias_v6address, alias.alias_v6address))
        self.fields['iscsi_target_portalip_ip'].choices = ips
        if not self.instance.id and not self.data:
            if not(
                self.parent and self.parent.instance.id and
                self.parent.instance.ips.all().count() > 0
            ) or (self.parent and not self.parent.instance.id):
                self.fields['iscsi_target_portalip_ip'].initial = '0.0.0.0'

    def clean(self):
        ip = self.cleaned_data.get('iscsi_target_portalip_ip')
        port = self.cleaned_data.get('iscsi_target_portalip_port')
        qs = models.iSCSITargetPortalIP.objects.filter(
            iscsi_target_portalip_ip=ip,
            iscsi_target_portalip_port=port,
        )
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            self._errors['__all__'] = self.error_class([
                _('This IP and port are already in use.'),
            ])
        return self.cleaned_data


class iSCSITargetAuthorizedInitiatorForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.iSCSITargetAuthorizedInitiator
        exclude = (
            'iscsi_target_initiator_tag',
        )

    def clean_iscsi_target_initiator_auth_network(self):
        field = self.cleaned_data.get(
            'iscsi_target_initiator_auth_network',
            '').strip().upper()
        nets = re.findall(r'\S+', field)

        for auth_network in nets:
            if auth_network == 'ALL':
                continue
            try:
                IPNetwork(auth_network.encode('utf-8'))
            except (NetmaskValueError, ValueError):
                try:
                    IPAddress(auth_network.encode('utf-8'))
                except (AddressValueError, ValueError):
                    raise forms.ValidationError(
                        _(
                            "The field is a not a valid IP address or network."
                            " The keyword \"ALL\" can be used to allow "
                            "everything.")
                    )
        return '\n'.join(nets)

    def save(self):
        o = super(iSCSITargetAuthorizedInitiatorForm, self).save(commit=False)
        i = models.iSCSITargetAuthorizedInitiator.objects.all().count() + 1
        while True:
            qs = models.iSCSITargetAuthorizedInitiator.objects.filter(
                iscsi_target_initiator_tag=i
            )
            if not qs.exists():
                break
            i += 1
        o.iscsi_target_initiator_tag = i
        o.save()
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(
            srv_service='iscsitarget'
        ).srv_enable:
            raise ServiceFailed(
                "iscsitarget", _("The iSCSI service failed to reload.")
            )


class iSCSITargetForm(ModelForm):

    iscsi_target_authgroup = forms.ChoiceField(label=_("Authentication Group number"))

    class Meta:
        fields = '__all__'
        model = models.iSCSITarget
        exclude = ('iscsi_target_initialdigest', 'iscsi_target_type')

    def __init__(self, *args, **kwargs):
        super(iSCSITargetForm, self).__init__(*args, **kwargs)
        if 'instance' not in kwargs:
            try:
                nic = list(choices.NICChoices(nolagg=True,
                                              novlan=True,
                                              exclude_configured=False))[0][0]
                mac = subprocess.Popen("ifconfig %s ether| grep ether | "
                                       "awk '{print $2}'|tr -d :" % (nic, ),
                                       shell=True,
                                       stdout=subprocess.PIPE).communicate()[0]
                ltg = models.iSCSITarget.objects.order_by('-id')
                if ltg.count() > 0:
                    lid = ltg[0].id
                else:
                    lid = 0
                self.fields['iscsi_target_serial'].initial = mac.strip() + "%.2d" % lid
            except:
                self.fields['iscsi_target_serial'].initial = "10000001"
        self.fields['iscsi_target_authgroup'].required = False
        self.fields['iscsi_target_authgroup'].choices = [(-1, _('None'))] + [(i['iscsi_target_auth_tag'], i['iscsi_target_auth_tag']) for i in models.iSCSITargetAuthCredential.objects.all().values('iscsi_target_auth_tag').distinct()]

    def clean_iscsi_target_name(self):
        name = self.cleaned_data.get("iscsi_target_name").lower()
        if not re.search(r'^[-a-z0-9\.:]+$', name):
            raise forms.ValidationError(_("Use alphanumeric characters, \".\", \"-\" and \":\"."))
        qs = models.iSCSITarget.objects.filter(iscsi_target_name=name)
        if self.instance.id:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise forms.ValidationError(
                _(u'A target with that name already exists.')
            )
        return name

    def clean_iscsi_target_authgroup(self):
        method = self.cleaned_data['iscsi_target_authtype']
        group = self.cleaned_data.get('iscsi_target_authgroup')
        if group in ('', None):
            return None
        if method == 'CHAP Mutual' and group:
            auth = models.iSCSITargetAuthCredential.objects.get(id=group)
            if not auth.iscsi_target_auth_peeruser:
                raise forms.ValidationError(_(
                    'This authentication group does not support CHAP MUTUAL'
                ))
        if method in ('CHAP', 'CHAP Mutual'):
            if group != '' and int(group) == -1:
                raise forms.ValidationError(_("This field is required."))
        elif group != '' and int(group) == -1:
            return None
        return int(group)

    def clean_iscsi_target_alias(self):
        alias = self.cleaned_data['iscsi_target_alias']
        if not alias:
            alias = None
        return alias

    def save(self):
        super(iSCSITargetForm, self).save()
        started = notifier().reload("iscsitarget")
        if started is False and models.services.objects.get(srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget", _("The iSCSI service failed to reload."))


class ExtentDelete(Form):
    delete = forms.BooleanField(
        label=_("Delete underlying file"),
        initial=False,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop('instance', None)
        super(ExtentDelete, self).__init__(*args, **kwargs)

    def done(self, *args, **kwargs):
        if (
            self.cleaned_data['delete']
            and
            self.instance.iscsi_target_extent_type == 'File'
            and
            os.path.exists(self.instance.iscsi_target_extent_path)
        ):
            os.unlink(self.instance.iscsi_target_extent_path)


class SMARTForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.SMART

    def clean_smart_email(self):
        email = self.cleaned_data.get("smart_email")
        if email:
            invalids = []
            for e in email.split(','):
                try:
                    validate_email(e.strip())
                except:
                    invalids.append(e.strip())

            if len(invalids) > 0:
                raise forms.ValidationError(ungettext_lazy(
                    'The email %(email)s is not valid',
                    'The following emails are not valid: %(email)s',
                    len(invalids)) % {
                        'email': ", ".join(invalids),
                    })
            else:
                email = email.replace(' ', '')
        return email

    def save(self):
        super(SMARTForm, self).save()
        started = notifier().restart("smartd")
        if started is False and models.services.objects.get(srv_service='smartd').srv_enable:
            raise ServiceFailed("smartd", _("The S.M.A.R.T. service failed to reload."))


class DomainControllerForm(ModelForm):
    dc_passwd2 = forms.CharField(
        max_length=50,
        label=_("Confirm Administrator Password"),
        widget=forms.widgets.PasswordInput(),
        required=False,
    )

    class Meta:
        fields = [
            'dc_realm',
            'dc_domain',
            'dc_role',
            'dc_dns_backend',
            'dc_dns_forwarder',
            'dc_forest_level',
            'dc_passwd',
            'dc_passwd2',
            'dc_kerberos_realm' 
        ]
        model = models.DomainController
        widgets = {
            'dc_passwd': forms.widgets.PasswordInput(render_value=False),
        }

    def __original_save(self):
        for name in ('dc_realm', 'dc_domain', 'dc_role', 'dc_passwd', 'dc_forest_level'):
            setattr(self.instance, "_original_%s" % name,
                getattr(self.instance, name)
            )

    def __original_changed(self):
        for name in ('dc_realm', 'dc_domain'):
            original_value = getattr(self.instance, "_original_%s" % name)
            instance_value = getattr(self.instance, name)
            if original_value != instance_value:
                return True
        return False

    def __dc_passwd_changed(self):
        if self.instance._original_dc_passwd != self.instance.dc_passwd:
            return True
        return False

    def __dc_forest_level_changed(self):
        if self.instance._original_dc_forest_level != self.instance.dc_forest_level:
            return True
        return False

    def __dc_domain_level_changed(self):
        return False

    def __init__(self, *args, **kwargs):
        super(DomainControllerForm, self).__init__(*args, **kwargs)
        if self.instance.dc_passwd:
            self.fields['dc_passwd'].required = False
        if self._api is True:
            del self.fields['dc_passwd2']

        self.__original_save()

    def clean_dc_passwd2(self):
        password1 = self.cleaned_data.get("dc_passwd")
        password2 = self.cleaned_data.get("dc_passwd2")
        if password1 != password2:
            raise forms.ValidationError(_("The two password fields didn't match."))
        return password2

    def clean(self):
        cdata = self.cleaned_data
        if not cdata.get("dc_passwd"):
            cdata['dc_passwd'] = self.instance.dc_passwd
        return cdata

    def save(self):
        super(DomainControllerForm, self).save()

        if self.__original_changed():
            Samba4().sentinel_file_remove()

        notifier().restart("domaincontroller")

        if self.__dc_forest_level_changed():
            Samba4().change_forest_level(self.instance.dc_forest_level)

        if self.__dc_passwd_changed():
            Samba4().set_administrator_password()

class WebDAVForm(ModelForm):
    webdav_password2 = forms.CharField(
        max_length=120,
        label=_("Confirm WebDAV Password"),
        widget=forms.widgets.PasswordInput(),
        required=False,
    )

    class Meta:
        fields = (
            'webdav_protocol', 'webdav_tcpport','webdav_tcpportssl',
            'webdav_certssl','webdav_htauth','webdav_password'
        )
        model = models.WebDAV
        widgets = {
            'webdav_tcpport' : forms.widgets.TextInput(),
            'webdav_tcpportssl' : forms.widgets.TextInput(),
            'webdav_password' : forms.widgets.PasswordInput(render_value=False), 
        }

    def __original_save(self):
        for name in ('webdav_password', 'webdav_tcpport','webdav_tcpportssl','webdav_protocol','webdav_htauth','webdav_certssl'):
            setattr(
                self.instance, "_original_%s" % name,
                getattr(self.instance, name)
            )

    def _has_changed(self,name):
        if getattr(self.instance, "_original_%s" % name) != getattr(self.instance, name):
            return True
        return False

    def __original_changed(self):
        for name in ('webdav_password', 'webdav_tcpport', 'webdav_protocol','webdav_tcpportssl','webdav_htauth','webdav_certssl'):
            original_value = getattr(self.instance, "_original_%s" % name)
            instance_value = getattr(self.instance, name)
            if original_value != instance_value:
                return True
        return False

    def __init__(self, *args, **kwargs):
        super(WebDAVForm, self).__init__(*args, **kwargs)
        if self.instance.webdav_password:
            self.fields['webdav_password'].required = False
        if self._api is True:
            del self.fields['webdav_password2']
        self.fields['webdav_protocol'].widget.attrs['onChange'] = (
            "webdavprotocolToggle();"
        )
        self.__original_save()

    def clean_webdav_password2(self):
        password1 = self.cleaned_data.get("webdav_password")
        password2 = self.cleaned_data.get("webdav_password2")
        if password1 != password2:
            raise forms.ValidationError(_("The two password fields didn't match."))
        return password2

    def clean(self):
        cdata = self.cleaned_data
        if not cdata.get("webdav_password"):
            cdata['webdav_password'] = self.instance.webdav_password
        if not cdata.get("webdav_tcpport"):
            cdata['webdav_tcpport'] = self.instance.webdav_tcpport
        if not cdata.get("webdav_tcpportssl"):
            cdata['webdav_tcpportssl'] = self.instance.webdav_tcpportssl
        if self.cleaned_data.get("webdav_tcpport") == self.cleaned_data.get("webdav_tcpportssl"):
            self._errors["webdav_tcpport"] = self.error_class([_("The HTTP and HTTPS ports cannot be the same!")])
        if (cdata.get("webdav_protocol") != 'http' and cdata.get("webdav_certssl") is None):
            raise forms.ValidationError(
                "Webdav SSL protocol specified without choosing a certificate")
        return cdata

    def save(self):
        obj = super(WebDAVForm,self).save(commit=False)
        obj.webdav_password = notifier().pwenc_encrypt(
            self.cleaned_data.get('webdav_password')
        )
        obj.save()
        if self.__original_changed():
            started = notifier().reload("webdav")
            if (started is False
                and
                models.services.objects.get(srv_service='webdav').srv_enable):
                    raise ServiceFailed( "webdav", _("The WebDAV service failed to reload."))

    def done(self, *args, **kwargs):
        if self._has_changed('webdav_certssl'):
            notifier().start_ssl("webdav")
