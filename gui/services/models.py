#+
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
import hashlib
import hmac
import logging
import re
import uuid

from django.db import models
from django.utils.translation import ugettext_lazy as _
from django.core.validators import (
    MinValueValidator, MaxValueValidator
)

from freenasUI import choices
from freenasUI.directoryservice.models import (
    KerberosRealm,
    idmap_tdb
)
from freenasUI.freeadmin.models import (
    Model, UserField, GroupField, PathField
)
from freenasUI.freeadmin.models.fields import MultiSelectField
from freenasUI.middleware.notifier import notifier
from freenasUI.services.exceptions import ServiceFailed
from freenasUI.storage.models import Disk
from freenasUI.system.models import Certificate

log = logging.getLogger("services.forms")


class services(Model):
    srv_service = models.CharField(
            max_length=120,
            verbose_name=_("Service"),
            help_text=_("Name of Service, should be auto-generated at build "
                "time")
            )
    srv_enable = models.BooleanField(
        verbose_name=_("Enable Service"),
        default=False,
    )

    class Meta:
        verbose_name = _("Services")
        verbose_name_plural = _("Services")

    def __unicode__(self):
        return self.srv_service

    def save(self, *args, **kwargs):
        super(services, self).save(*args, **kwargs)


class CIFS(Model):
    cifs_srv_netbiosname = models.CharField(
            max_length=120,
            verbose_name=_("NetBIOS name")
            )
    cifs_srv_workgroup = models.CharField(
            max_length=120,
            verbose_name=_("Workgroup"),
            help_text=_("Workgroup the server will appear to be in when "
                "queried by clients (maximum 15 characters).")
            )
    cifs_srv_description = models.CharField(
            max_length=120,
            verbose_name=_("Description"),
            blank=True,
            help_text=_("Server description. This can usually be left blank.")
            )
    cifs_srv_doscharset = models.CharField(
            max_length=120,
            choices=choices.DOSCHARSET_CHOICES,
            default="CP437",
            verbose_name=_("DOS charset")
            )
    cifs_srv_unixcharset = models.CharField(
            max_length=120,
            choices=choices.UNIXCHARSET_CHOICES,
            default="UTF-8",
            verbose_name=_("UNIX charset")
            )
    cifs_srv_loglevel = models.CharField(
            max_length=120,
            choices=choices.LOGLEVEL_CHOICES,
            default=choices.LOGLEVEL_CHOICES[0][0],
            verbose_name=_("Log level")
            )
    cifs_srv_syslog = models.BooleanField(
        verbose_name=_("Use syslog"),
        default=False,
    )
    cifs_srv_localmaster = models.BooleanField(
        verbose_name=_("Local Master"),
        default=False,
    )
    cifs_srv_domain_logons = models.BooleanField(
        verbose_name=_("Domain logons"),
        default=False,
    )
    cifs_srv_timeserver = models.BooleanField(
        verbose_name=_("Time Server for Domain"),
        default=False,
    )
    cifs_srv_guest = UserField(
            max_length=120,
            default="nobody",
            exclude=["root"],
            verbose_name=_("Guest account"),
            help_text=_("Use this option to override the username ('nobody' "
                "by default) which will be used for access to services which "
                "are specified as guest. Whatever privileges this user has "
                "will be available to any client connecting to the guest "
                "service. This user must exist in the password file, but does "
                "not require a valid login. The user root can not be used as "
                "guest account.")
            )
    cifs_srv_filemask = models.CharField(
            max_length=120,
            verbose_name=_("File mask"),
            blank=True,
            help_text=_("Use this option to override the file creation mask "
                "(0666 by default).")
            )
    cifs_srv_dirmask = models.CharField(
            max_length=120,
            verbose_name=_("Directory mask"),
            blank=True,
            help_text=_("Use this option to override the directory creation "
                "mask (0777 by default).")
            )
    cifs_srv_nullpw = models.BooleanField(
        verbose_name=_("Allow Empty Password"),
        default=False,
    )
    cifs_srv_smb_options = models.TextField(
            verbose_name=_("Auxiliary parameters"),
            blank=True,
            help_text=_("These parameters are added to [global] section of "
                "smb.conf")
            )
    cifs_srv_unixext = models.BooleanField(
            verbose_name=_("Unix Extensions"),
            default=True,
            help_text=_("These extensions enable Samba to better serve UNIX "
                "CIFS clients by supporting features such as symbolic links, "
                "hard links, etc..."),
            )
    cifs_srv_aio_enable = models.BooleanField(
            default=False,
            verbose_name=_("Enable AIO"),
            editable=False,
            help_text=_("Enable/disable AIO support.")
            )
    cifs_srv_aio_rs = models.IntegerField(
            max_length=120,
            verbose_name=_("Minimum AIO read size"),
            help_text=_("Samba will read asynchronously if request size is "
                "larger than this value."),
            default=4096,
            editable=False,
            )
    cifs_srv_aio_ws = models.IntegerField(
            max_length=120,
            verbose_name=_("Minimum AIO write size"),
            help_text=_("Samba will write asynchronously if request size is "
                "larger than this value."),
            default=4096,
            editable=False,
            )
    cifs_srv_zeroconf = models.BooleanField(
            verbose_name=_("Zeroconf share discovery"),
            default=True,
            help_text=_("Zeroconf support via Avahi allows clients (the Mac "
                "OSX finder in particular) to automatically discover the CIFS "
                "shares on the system similar to the Computer Browser service "
                "in Windows."),
            )
    cifs_srv_hostlookup = models.BooleanField(
            verbose_name=_("Hostnames lookups"),
            default=True,
            help_text=_("Specifies whether samba should use (expensive) "
                "hostname lookups or use the ip addresses instead. An example "
                "place where hostname lookups are currently used is when "
                "checking the hosts deny and hosts allow."),
            )
    cifs_srv_min_protocol = models.CharField(
            max_length=120,
            verbose_name=_("Server minimum protocol"),
            choices=choices.CIFS_SMB_PROTO_CHOICES,
            help_text=_("The minimum protocol version that will be supported by the server"),
            blank=True
            )
    cifs_srv_max_protocol = models.CharField(
            max_length=120,
            verbose_name=_("Server maximum protocol"),
            default='SMB2',
            choices=choices.CIFS_SMB_PROTO_CHOICES,
            help_text=_("The highest protocol version that will be supported by the server")
            )
    cifs_srv_allow_execute_always = models.BooleanField(
            verbose_name=_("Allow execute always"),
            default=True,
            help_text=_("This boolean parameter controls the behaviour of smbd(8) when "
                "receiving a protocol request of \"open for execution\" from a Windows "
                "client. With Samba 3.6 and older, the execution right in the ACL "
                "was not checked, so a client could execute a file even if it did "
                "not have execute rights on the file. In Samba 4.0, this has been "
                "fixed, so that by default, i.e. when this parameter is set to "
                "\"False\", \"open for execution\" is now denied when execution "
                "permissions are not present. If this parameter is set to \"True\", "
                "Samba does not check execute permissions on \"open for execution\", "
                "thus re-establishing the behaviour of Samba 3.6 ")
           )
    cifs_srv_obey_pam_restrictions = models.BooleanField(
           verbose_name=_("Obey pam restrictions"),
           default=True,
           help_text=_("This parameter controls whether or not Samba"
               " should obey PAM's account and session management directives")
           )
    cifs_srv_bindip = models.CharField(
           verbose_name=_("Bind IP Addresses"),
           help_text=_("IP address(es) to bind to. If none specified, all"
               " available interfaces that are up will be listened on."),
           max_length=250,
           blank=True,
           null=True
           )
    cifs_SID = models.CharField(
           max_length=120,
           blank=True,
           null=True,
           )

    class Meta:
        verbose_name = _(u"CIFS")
        verbose_name_plural = _(u"CIFS")

    class FreeAdmin:
        deletable = False
        icon_model = u"CIFSIcon"


class AFP(Model):
    afp_srv_guest = models.BooleanField(
        verbose_name=_("Guest Access"),
        help_text=_("Allows guest access to all apple shares on this box."),
        default=False,
    )
    afp_srv_guest_user = UserField(
            max_length=120,
            default="nobody",
            exclude=["root"],
            verbose_name=_("Guest account"),
            help_text=_("Use this option to override the username ('nobody' by"
                " default) which will be used for access to services which are"
                " specified as guest. Whatever privileges this user has will "
                "be available to any client connecting to the guest service. "
                "This user must exist in the password file, but does not "
                "require a valid login. The user root can not be used as guest"
                " account.")
            )
    afp_srv_bindip = MultiSelectField(
        verbose_name=_("Bind IP Addresses"),
        help_text=_(
            "IP address(es) to advertise and listens to. If none specified, "
            "advertise the first IP address of the system, but to listen for "
            "any incoming request."
        ),
        max_length=255,
        blank=True,
        choices=list(choices.IPChoices()),
        default='',
    )
    afp_srv_connections_limit = models.IntegerField(
            max_length=120,
            verbose_name=_('Max. Connections'),
            validators=[MinValueValidator(1), MaxValueValidator(1000)],
            help_text=_("Maximum number of connections permitted via AFP. The "
                "default limit is 50."),
            default=50,
            )
    afp_srv_homedir_enable = models.BooleanField(
        verbose_name=_("Enable home directories"),
        help_text=_("Enable/disable home directories for afp user."),
        default=False,
    )
    afp_srv_homedir = PathField(
            verbose_name=_("Home directories"),
            blank=True,
            )
    afp_srv_dbpath = PathField(
        verbose_name=_('Database Path'),
        blank=True,
        help_text=_(
            'Sets the database information to be stored in path. You have to '
            'specify a writable location, even if the volume is read only.'
        ),
    )
    afp_srv_global_aux = models.TextField(
        verbose_name=_("Global auxiliary parameters"),
        blank=True,
        help_text=_(
            "These parameters are added to [Global] section of afp.conf"
        )
    )

    class Meta:
        verbose_name = _(u"AFP")
        verbose_name_plural = _(u"AFP")

    class FreeAdmin:
        deletable = False
        icon_model = u"AFPIcon"


class NFS(Model):
    nfs_srv_servers = models.PositiveIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(256)],
        verbose_name=_("Number of servers"),
        help_text=_("Specifies how many servers to create. There should be"
            " enough to handle the maximum level of concurrency from its "
            "clients, typically four to six."
        )
    )
    nfs_srv_udp = models.BooleanField(
        verbose_name=_('Serve UDP NFS clients'),
        default=False,
    )
    nfs_srv_allow_nonroot = models.BooleanField(
        default=False,
        verbose_name=_("Allow non-root mount"),
        help_text=_("Allow non-root mount requests to be served. This should "
            "only be specified if there are clients that require it.  It will "
            "automatically clear the vfs.nfsrv.nfs_privport sysctl flag, "
            "which controls if the kernel will accept NFS requests from "
            "reserved ports only."
        ),
    )
    nfs_srv_v4 = models.BooleanField(
        default=False,
        verbose_name=_("Enable NFSv4"),
    )
    nfs_srv_bindip = models.CharField(
        blank=True,
        max_length=250,
        verbose_name=_("Bind IP Addresses"),
        help_text=_("Specify specific IP addresses (separated by commas) to "
            "bind to for TCP and UDP requests. This option may be specified "
            "multiple times. If no IP is specified it will bind to INADDR_ANY."
            " It will automatically add 127.0.0.1 and if IPv6 is enabled, ::1 "
            "to the list."
        )
    )
    nfs_srv_mountd_port = models.SmallIntegerField(
        verbose_name=_("mountd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "Force mountd to bind to the specified port, for both IPv4 and "
            "IPv6 address families. This is typically done to ensure that "
            "the port which mountd binds to is a known value which can be "
            "used in firewall rulesets."
        )
    )
    nfs_srv_rpcstatd_port = models.SmallIntegerField(
        verbose_name=_("rpc.statd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "This option forces the rpc.statd daemon to bind to the specified "
            "port, for both IPv4 and IPv6 address families."
        )
    )
    nfs_srv_rpclockd_port = models.SmallIntegerField(
        verbose_name=_("rpc.lockd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "This option forces rpc.lockd the daemon to bind to the specified "
            "port, for both IPv4 and IPv6 address families."
        )
    )

    class Meta:
        verbose_name = _("NFS")
        verbose_name_plural = _("NFS")


class iSCSITargetGlobalConfiguration(Model):
    iscsi_basename = models.CharField(
            max_length=120,
            verbose_name=_("Base Name"),
            help_text=_("The base name (e.g. iqn.2007-09.jp.ne.peach.istgt, "
                "see RFC 3720 and 3721 for details) will append the target "
                "name that is not starting with 'iqn.'")
            )
    iscsi_discoveryauthmethod = models.CharField(
            max_length=120,
            choices=choices.AUTHMETHOD_CHOICES,
            default='None',
            verbose_name=_("Discovery Auth Method")
            )
    iscsi_discoveryauthgroup = models.IntegerField(
            max_length=120,
            verbose_name=_("Discovery Auth Group"),
            blank=True,
            null=True,
            )
    iscsi_isns_servers = models.TextField(
        verbose_name=_('iSNS Servers'),
        blank=True,
    )
    iscsi_pool_avail_threshold = models.IntegerField(
        verbose_name=_('Pool Available Size Threshold (%)'),
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(99)],
    )

    class Meta:
        verbose_name = _(u"Target Global Configuration")
        verbose_name_plural = _(u"Target Global Configuration")

    class FreeAdmin:
        deletable = False
        menu_child_of = "sharing.ISCSI"
        icon_model = u"SettingsIcon"
        nav_extra = {'type': 'iscsi', 'order': -10}
        resource_name = 'services/iscsi/globalconfiguration'


class iSCSITargetExtent(Model):
    iscsi_target_extent_name = models.CharField(
            max_length=120,
            unique=True,
            verbose_name=_("Extent Name"),
            help_text=_("String identifier of the extent."),
            )
    iscsi_target_extent_type = models.CharField(
            max_length=120,
            verbose_name=_("Extent Type"),
            help_text=_("Type used as extent."),
            )
    iscsi_target_extent_path = models.CharField(
            max_length=120,
            verbose_name=_("Path to the extent"),
            help_text=_("File path (e.g. /mnt/sharename/extent/extent0) "
                "used as extent."),
            )
    iscsi_target_extent_filesize = models.CharField(
            max_length=120,
            default=0,
            verbose_name=_("Extent size"),
            help_text=_("Size of extent, 0 means auto, a raw number is bytes"
                ", or suffix with KB, MB, TB for convenience."),
            )
    iscsi_target_extent_avail_threshold = models.IntegerField(
        verbose_name=_(' Available Size Threshold (%)'),
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(99)],
    )
    iscsi_target_extent_comment = models.CharField(
            blank=True,
            max_length=120,
            verbose_name=_("Comment"),
            help_text=_("You may enter a description here for your "
                "reference."),
            )
    iscsi_target_extent_naa = models.CharField(
            blank=True,
            editable=False,
            unique=True,
            max_length=18,
            verbose_name=_("NAA...used only by the initiator"),
            )
    iscsi_target_extent_insecure_tpc = models.BooleanField(
            default=True,
            verbose_name=_("Enable TPC"),
            help_text=_("Allow initiators to xcopy without authenticating to foreign targets."),
            )

    class Meta:
        verbose_name = _("Extent")
        ordering = ["iscsi_target_extent_name"]

    def __unicode__(self):
        return unicode(self.iscsi_target_extent_name)

    def get_device(self):
        if self.iscsi_target_extent_type not in ("Disk", "ZVOL"):
            return self.iscsi_target_extent_path
        else:
            try:
                disk = Disk.objects.get(id=self.iscsi_target_extent_path)
                if disk.disk_multipath_name:
                    return "/dev/%s" % disk.devname
                else:
                    return "/dev/%s" % (
                        notifier().identifier_to_device(disk.disk_identifier),
                        )
            except:
                return self.iscsi_target_extent_path

    def delete(self):
        if self.iscsi_target_extent_type in ("Disk", "ZVOL"):
            try:
                if self.iscsi_target_extent_type == "Disk":
                    disk = Disk.objects.get(id=self.iscsi_target_extent_path)
                    devname = disk.identifier_to_device()
                    if not devname:
                        disk.disk_enabled = False
                        disk.save()
            except Exception, e:
                log.error("Unable to sync iSCSI extent delete: %s", e)

        for te in iSCSITargetToExtent.objects.filter(iscsi_extent=self):
            te.delete()
        super(iSCSITargetExtent, self).delete()

    def save(self, *args, **kwargs):
        if not self.iscsi_target_extent_naa:
            self.iscsi_target_extent_naa = '0x3%s' % (
                hashlib.sha256(str(uuid.uuid4())).hexdigest()[0:15]
            )
        return super(iSCSITargetExtent, self).save(*args, **kwargs)


class iSCSITargetPortal(Model):
    iscsi_target_portal_tag = models.IntegerField(
            max_length=120,
            default=1,
            verbose_name=_("Portal Group ID"),
            )
    iscsi_target_portal_comment = models.CharField(
            max_length=120,
            blank=True,
            verbose_name=_("Comment"),
            help_text=_("You may enter a description here for your reference.")
            )

    class Meta:
        verbose_name = _("Portal")

    def __unicode__(self):
        if self.iscsi_target_portal_comment != "":
            return u"%s (%s)" % (
                self.iscsi_target_portal_tag,
                self.iscsi_target_portal_comment,
                )
        else:
            return unicode(self.iscsi_target_portal_tag)

    def delete(self):
        super(iSCSITargetPortal, self).delete()
        portals = iSCSITargetPortal.objects.all().order_by(
            'iscsi_target_portal_tag')
        for portal, idx in zip(portals, xrange(1, len(portals) + 1)):
            portal.iscsi_target_portal_tag = idx
            portal.save()
        started = notifier().reload("iscsitarget")
        if started is False and services.objects.get(
                srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget",
                _("The iSCSI service failed to reload."))


class iSCSITargetPortalIP(Model):
    iscsi_target_portalip_portal = models.ForeignKey(
            iSCSITargetPortal,
            verbose_name=_("Portal"),
            related_name='ips',
            )
    iscsi_target_portalip_ip = models.IPAddressField(
            verbose_name=_("IP Address"),
            )
    iscsi_target_portalip_port = models.SmallIntegerField(
            verbose_name=_("Port"),
            default=3260,
            validators=[MinValueValidator(1), MaxValueValidator(65535)],
            )

    class Meta:
        unique_together = (
            ('iscsi_target_portalip_ip', 'iscsi_target_portalip_port'),
            )
        verbose_name = _("Portal IP")

    def __unicode__(self):
        return "%s:%d" % (
            self.iscsi_target_portalip_ip,
            self.iscsi_target_portalip_port,
        )


class iSCSITargetAuthorizedInitiator(Model):
    iscsi_target_initiator_tag = models.IntegerField(
            default=1,
            unique=True,
            verbose_name=_("Group ID"),
            )
    iscsi_target_initiator_initiators = models.TextField(
            max_length=2048,
            verbose_name=_("Initiators"),
            default="ALL",
            help_text=_("Initiator authorized to access to the iSCSI target."
                " It takes a name or 'ALL' for any initiators.")
            )
    iscsi_target_initiator_auth_network = models.TextField(
            max_length=2048,
            verbose_name=_("Authorized network"),
            default="ALL",
            help_text=_("Network authorized to access to the iSCSI target. "
                "It takes IP or CIDR addresses or 'ALL' for any IPs.")
            )
    iscsi_target_initiator_comment = models.CharField(
            max_length=120,
            blank=True,
            verbose_name=_("Comment"),
            help_text=_("You may enter a description here for your reference.")
            )

    class Meta:
        verbose_name = _("Initiator")

    class FreeAdmin:
        menu_child_of = "sharing.ISCSI"
        icon_object = u"InitiatorIcon"
        icon_model = u"InitiatorIcon"
        icon_add = u"AddInitiatorIcon"
        icon_view = u"ViewAllInitiatorsIcon"
        nav_extra = {'order': 0}
        resource_name = 'services/iscsi/authorizedinitiator'

    def __unicode__(self):
        if self.iscsi_target_initiator_comment != "":
            return u"%s (%s)" % (
                self.iscsi_target_initiator_tag,
                self.iscsi_target_initiator_comment,
                )
        else:
            return unicode(self.iscsi_target_initiator_tag)

    def delete(self):
        super(iSCSITargetAuthorizedInitiator, self).delete()
        portals = iSCSITargetAuthorizedInitiator.objects.all().order_by(
            'iscsi_target_initiator_tag')
        idx = 1
        for portal in portals:
            portal.iscsi_target_initiator_tag = idx
            portal.save()
            idx += 1


class iSCSITargetAuthCredential(Model):
    iscsi_target_auth_tag = models.IntegerField(
            default=1,
            verbose_name=_("Group ID"),
            )
    iscsi_target_auth_user = models.CharField(
            max_length=120,
            verbose_name=_("User"),
            help_text=_("Target side user name. It is usually the initiator "
                "name by default."),
            )
    iscsi_target_auth_secret = models.CharField(
            max_length=120,
            verbose_name=_("Secret"),
            help_text=_("Target side secret."),
            )
    iscsi_target_auth_peeruser = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Peer User"),
        help_text=_("Initiator side user name."),
    )
    iscsi_target_auth_peersecret = models.CharField(
        max_length=120,
        verbose_name=_("Peer Secret"),
        blank=True,
        help_text=_("Initiator side secret. (for mutual CHAP authentication)"),
    )

    class Meta:
        verbose_name = _("Authorized Access")
        verbose_name_plural = _("Authorized Accesses")

    def __unicode__(self):
        return unicode(self.iscsi_target_auth_tag)


class iSCSITarget(Model):
    iscsi_target_name = models.CharField(
            unique=True,
            max_length=120,
            verbose_name=_("Target Name"),
            help_text=_("Base Name will be appended automatically when "
                "starting without 'iqn.'."),
            )
    iscsi_target_alias = models.CharField(
            unique=True,
            blank=True,
            null=True,
            max_length=120,
            verbose_name=_("Target Alias"),
            help_text=_("Optional user-friendly string of the target."),
            )
    iscsi_target_serial = models.CharField(
            verbose_name=_("Serial"),
            max_length=16,
            default="10000001",
            help_text=_("Serial number for the logical unit")
            )
    iscsi_target_portalgroup = models.ForeignKey(
            iSCSITargetPortal,
            verbose_name=_("Portal Group ID"),
            )
    iscsi_target_initiatorgroup = models.ForeignKey(
        iSCSITargetAuthorizedInitiator,
        verbose_name=_("Initiator Group ID"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        )
    iscsi_target_authtype = models.CharField(
            max_length=120,
            choices=choices.AUTHMETHOD_CHOICES,
            default="None",
            verbose_name=_("Auth Method"),
            help_text=_("The authentication method accepted by the target."),
            )
    iscsi_target_authgroup = models.IntegerField(
            max_length=120,
            verbose_name=_("Authentication Group ID"),
            null=True,
            blank=True,
            )
    iscsi_target_initialdigest = models.CharField(
            max_length=120,
            default="Auto",
            verbose_name=_("Auth Method"),
            help_text=_("The method can be accepted by the target. Auto means "
                "both none and authentication."),
            )
    iscsi_target_logical_blocksize = models.IntegerField(
            max_length=4,
            choices=choices.TARGET_BLOCKSIZE_CHOICES,
            default=choices.TARGET_BLOCKSIZE_CHOICES[0][0],
            verbose_name=_("Logical Block Size"),
            help_text=_("You may specify logical block length (512 by "
                "default). The recommended length for compatibility is 512."),
            )

    class Meta:
        verbose_name = _("Target")
        ordering = ['iscsi_target_name']

    def __unicode__(self):
        return self.iscsi_target_name

    def delete(self):
        for te in iSCSITargetToExtent.objects.filter(iscsi_target=self):
            te.delete()
        super(iSCSITarget, self).delete()


class iSCSITargetToExtent(Model):
    iscsi_lunid = models.IntegerField(
        verbose_name=_('LUN ID'),
        null=True,
    )
    iscsi_target = models.ForeignKey(
        iSCSITarget,
        verbose_name=_("Target"),
        help_text=_("Target this extent belongs to"),
    )
    iscsi_extent = models.ForeignKey(
        iSCSITargetExtent,
        unique=True,
        verbose_name=_("Extent"),
    )

    class Meta:
        verbose_name = _("Target / Extent")
        verbose_name_plural = _("Targets / Extents")

    def __unicode__(self):
        return unicode(self.iscsi_target) + u' / ' + unicode(self.iscsi_extent)

    def delete(self):
        super(iSCSITargetToExtent, self).delete()
        started = notifier().reload("iscsitarget")
        if started is False and services.objects.get(
                srv_service='iscsitarget').srv_enable:
            raise ServiceFailed("iscsitarget",
                _("The iSCSI service failed to reload."))


class DynamicDNS(Model):
    ddns_provider = models.CharField(
            max_length=120,
            choices=choices.DYNDNSPROVIDER_CHOICES,
            default=choices.DYNDNSPROVIDER_CHOICES[0][0],
            blank=True,
            verbose_name=_("Provider")
            )
    ddns_ipserver = models.CharField(
        max_length=150,
        verbose_name=_('IP Server'),
        help_text=_(
            'The client IP is detected by calling \'url\' from this '
            '\'ip_server_name:port\'. Defaults to checkip.dyndns.org:80 /.'
        ),
        blank=True,
    )
    ddns_domain = models.CharField(
            max_length=120,
            verbose_name=_("Domain name"),
            blank=True,
            help_text=_("A host name alias. This option can appear multiple "
                "times, for each domain that has the same IP. Use a comma to "
                "separate multiple alias names.")
            )
    ddns_username = models.CharField(
            max_length=120,
            verbose_name=_("Username")
            )
    ddns_password = models.CharField(
            max_length=120,
            verbose_name=_("Password")
            )
    ddns_updateperiod = models.CharField(
            max_length=120,
            verbose_name=_("Update period"),
            blank=True,
            help_text=_("Time in seconds. Default is about 1 min.")
            )
    ddns_fupdateperiod = models.CharField(
            max_length=120,
            verbose_name=_("Forced update period"),
            blank=True
            )
    ddns_options = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        blank=True,
        help_text=_(
            "These parameters will be added to global settings in "
            "inadyn-mt.conf."
        ),
    )

    class Meta:
        verbose_name = _("Dynamic DNS")
        verbose_name_plural = _("Dynamic DNS")

    class FreeAdmin:
        deletable = False
        icon_model = u"DDNSIcon"


class SNMP(Model):
    snmp_location = models.CharField(
            max_length=255,
            verbose_name=_("Location"),
            blank=True,
            help_text=_("Location information, e.g. physical location of this "
                "system: 'Floor of building, Room xyzzy'.")
            )
    snmp_contact = models.CharField(
            max_length=120,
            verbose_name=_("Contact"),
            blank=True,
            help_text=_("Contact information, e.g. name or email of the "
                "person responsible for this system: "
                "'admin@email.address'.")
            )
    snmp_community = models.CharField(
        max_length=120,
        default='public',
        verbose_name=_("Community"),
        help_text=_("In most cases, 'public' is used here.")
    )
    #FIXME: Implement trap
    snmp_traps = models.BooleanField(
        verbose_name=_("Send SNMP Traps"),
        editable=False,
        default=False,
    )
    snmp_options = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        blank=True,
        help_text=_("These parameters will be added to /etc/snmpd.config.")
    )

    class Meta:
        verbose_name = _("SNMP")
        verbose_name_plural = _("SNMP")

    class FreeAdmin:
        deletable = False
        icon_model = u"SNMPIcon"
        #advanced_fields = ('snmp_traps',)


class UPS(Model):
    ups_mode = models.CharField(
        default='master',
        max_length=6,
        choices=(
            ('master', _("Master")),
            ('slave', _("Slave")),
        ),
        verbose_name=_("UPS Mode")
    )
    ups_identifier = models.CharField(
        max_length=120,
        verbose_name=_("Identifier"),
        default='ups',
        help_text=_(
            "This name is used to uniquely identify your UPS on this system."
        ),
    )
    ups_remotehost = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Remote Host")
    )
    ups_remoteport = models.IntegerField(
        default=3493,
        blank=True,
        verbose_name=_("Remote Port")
    )
    ups_driver = models.CharField(
            max_length=120,
            verbose_name=_("Driver"),
            choices=choices.UPSDRIVER_CHOICES(),
            blank=True,
            help_text=_("The driver used to communicate with your UPS.")
            )
    ups_port = models.CharField(
            max_length=120,
            verbose_name=_("Port"),
            blank=True,
            help_text=_("The serial or USB port where your UPS is connected.")
            )
    ups_options = models.TextField(
            verbose_name=_("Auxiliary parameters (ups.conf)"),
            blank=True,
            help_text=_("Additional parameters to the hardware-specific part "
                "of the driver.")
            )
    ups_description = models.CharField(
            max_length=120,
            verbose_name=_("Description"),
            blank=True
            )
    ups_shutdown = models.CharField(
            max_length=120,
            choices=choices.UPS_CHOICES,
            default='batt',
            verbose_name=_("Shutdown mode")
            )
    ups_shutdowntimer = models.IntegerField(
        verbose_name=_("Shutdown timer"),
        default=30,
        help_text=_(
            "The time in seconds until shutdown is initiated. If the UPS "
            "happens to come back before the time is up the "
            "shutdown is canceled."
        ),
    )
    ups_monuser = models.CharField(
        max_length=50,
        default='upsmon',
        verbose_name=_("Monitor User")
    )
    ups_monpwd = models.CharField(
        max_length=30,
        default="fixmepass",
        verbose_name=_("Monitor Password"),
    )
    ups_extrausers = models.TextField(
            blank=True,
            verbose_name=_("Extra users (upsd.users)"),
            )
    ups_rmonitor = models.BooleanField(
        verbose_name=_("Remote Monitor"),
        default=False,
    )
    ups_emailnotify = models.BooleanField(
        verbose_name=_("Send Email Status Updates"),
        default=False,
    )
    ups_toemail = models.CharField(
            max_length=120,
            verbose_name=_("To email"),
            blank=True,
            help_text=_("Destination email address. Separate email addresses "
                "by semi-colon.")
            )
    ups_subject = models.CharField(
        max_length=120,
        verbose_name=_("Email Subject"),
        default='UPS report generated by %h',
        help_text=_(
            "The subject of the email. You can use the following "
            "parameters for substitution:<br /><ul><li>%d - Date</li><li>"
            "%h - Hostname</li></ul>"
        )
    )

    class Meta:
        verbose_name = _("UPS")
        verbose_name_plural = _("UPS")

    class FreeAdmin:
        deletable = False
        icon_model = u"UPSIcon"


class FTP(Model):
    ftp_port = models.PositiveIntegerField(
            default=21,
            verbose_name=_("Port"),
            validators=[MinValueValidator(1), MaxValueValidator(65535)],
            help_text=_("Port to bind FTP server.")
            )
    ftp_clients = models.PositiveIntegerField(
            default=32,
            verbose_name=_("Clients"),
            validators=[MinValueValidator(0), MaxValueValidator(10000)],
            help_text=_("Maximum number of simultaneous clients.")
            )
    ftp_ipconnections = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Connections"),
            validators=[MinValueValidator(0), MaxValueValidator(1000)],
            help_text=_("Maximum number of connections per IP address "
                "(0 = unlimited).")
            )
    ftp_loginattempt = models.PositiveIntegerField(
            default=3,
            verbose_name=_("Login Attempts"),
            validators=[MinValueValidator(0), MaxValueValidator(1000)],
            help_text=_("Maximum number of allowed password attempts before "
                "disconnection.")
            )
    ftp_timeout = models.PositiveIntegerField(
            default=120,
            verbose_name=_("Timeout"),
            validators=[MinValueValidator(0), MaxValueValidator(10000)],
            help_text=_("Maximum idle time in seconds.")
            )
    ftp_rootlogin = models.BooleanField(
        verbose_name=_("Allow Root Login"),
        default=False,
    )
    ftp_onlyanonymous = models.BooleanField(
        verbose_name=_("Allow Anonymous Login"),
        default=False,
    )
    ftp_anonpath = PathField(
        blank=True,
        verbose_name=_("Path"))
    ftp_onlylocal = models.BooleanField(
        verbose_name=_("Allow Local User Login"),
        default=False,
    )
    #FIXME: rename the field
    ftp_banner = models.TextField(
        max_length=120,
        verbose_name=_("Display Login"),
        blank=True,
        help_text=_(
            "Message which will be displayed to the user when they initially "
            "login."
        ),
    )
    ftp_filemask = models.CharField(
            max_length=3,
            default="077",
            verbose_name=_("File mask"),
            help_text=_("Use this option to override the file creation mask "
                "(077 by default).")
            )
    ftp_dirmask = models.CharField(
        max_length=3,
        default="077",
        verbose_name=_("Directory mask"),
        help_text=_(
            "Use this option to override the directory creation mask "
            "(077 by default)."
        ),
    )
    ftp_fxp = models.BooleanField(
        verbose_name=_("Enable FXP"),
        default=False,
    )
    ftp_resume = models.BooleanField(
        verbose_name=_("Allow Transfer Resumption"),
        default=False,
    )
    ftp_defaultroot = models.BooleanField(
        verbose_name=_("Always Chroot"),
        help_text=_(
            "For local users, only allow access to user home directory unless "
            "the user is a member of group wheel."
        ),
        default=False,
    )
    ftp_ident = models.BooleanField(
        verbose_name=_("Require IDENT Authentication"),
        default=False,
    )
    ftp_reversedns = models.BooleanField(
        verbose_name=_("Perform Reverse DNS Lookups"),
        default=False,
    )
    ftp_masqaddress = models.CharField(
            verbose_name=_("Masquerade address"),
            blank=True,
            max_length=120,
            help_text=_("Causes the server to display the network information "
                "for the specified address to the client, on the assumption "
                "that IP address or DNS host is acting as a NAT gateway or "
                "port forwarder for the server.")
            )
    ftp_passiveportsmin = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Minimum passive port"),
            help_text=_("The minimum port to allocate for PASV style data "
                "connections (0 = use any port).")
            )
    ftp_passiveportsmax = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Maximum passive port"),
            help_text=_("The maximum port to allocate for PASV style data "
                "connections (0 = use any port). Passive ports restricts the "
                "range of ports from which the server will select when sent "
                "the PASV command from a client. The server will randomly "
                "choose a number from within the specified range until an open"
                " port is found. The port range selected must be in the "
                "non-privileged range (eg. greater than or equal to 1024). It "
                "is strongly recommended that the chosen range be large enough"
                " to handle many simultaneous passive connections (for example"
                ", 49152-65534, the IANA-registered ephemeral port range).")
            )
    ftp_localuserbw = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Local user upload bandwidth"),
            help_text=_("Local user upload bandwidth in KB/s. Zero means "
                "infinity.")
            )
    ftp_localuserdlbw = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Local user download bandwidth"),
            help_text=_("Local user download bandwidth in KB/s. Zero means "
                "infinity.")
            )
    ftp_anonuserbw = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Anonymous user upload bandwidth"),
            help_text=_("Anonymous user upload bandwidth in KB/s. Zero means "
                "infinity.")
            )
    ftp_anonuserdlbw = models.PositiveIntegerField(
            default=0,
            verbose_name=_("Anonymous user download bandwidth"),
            help_text=_("Anonymous user download bandwidth in KB/s. Zero means"
                " infinity.")
            )
    ftp_tls = models.BooleanField(
        verbose_name=_("Enable TLS"),
        default=False,
    )
    ftp_tls_policy = models.CharField(
            max_length=120,
            choices=choices.FTP_TLS_POLICY_CHOICES,
            default="on",
            verbose_name=_("TLS policy")
            )
    ftp_tls_opt_allow_client_renegotiations = models.BooleanField(
            verbose_name=_("TLS allow client renegotiations"),
            default=False
            )
    ftp_tls_opt_allow_dot_login = models.BooleanField(
            verbose_name=_("TLS allow dot login"),
            default=False
            )
    ftp_tls_opt_allow_per_user = models.BooleanField(
            verbose_name=_("TLS allow per user"),
            default=False
            )
    ftp_tls_opt_common_name_required = models.BooleanField(
            verbose_name=_("TLS common name required"),
            default=False
            )
    ftp_tls_opt_enable_diags = models.BooleanField(
            verbose_name=_("TLS enable diagnostics"),
            default=False
            )
    ftp_tls_opt_export_cert_data = models.BooleanField(
            verbose_name=_("TLS export certificate data"),
            default=False
            )
    ftp_tls_opt_no_cert_request = models.BooleanField(
            verbose_name=_("TLS no certificate request"),
            default=False
            )
    ftp_tls_opt_no_empty_fragments = models.BooleanField(
            verbose_name=_("TLS no empty fragments"),
            default=False
            )
    ftp_tls_opt_no_session_reuse_required = models.BooleanField(
            verbose_name=_("TLS no session reuse required"),
            default=False
            )
    ftp_tls_opt_stdenvvars = models.BooleanField(
            verbose_name=_("TLS export standard vars"),
            default=False
            )
    ftp_tls_opt_dns_name_required = models.BooleanField(
            verbose_name=_("TLS DNS name required"),
            default=False
            )
    ftp_tls_opt_ip_address_required = models.BooleanField(
            verbose_name=_("TLS IP address required"),
            default=False
            )
    ftp_ssltls_certificate = models.ForeignKey(
            Certificate,
            verbose_name=_("Certificate"),
            on_delete=models.SET_NULL,
            blank=True,
            null=True
            )
    ftp_options = models.TextField(
            max_length=120,
            verbose_name=_("Auxiliary parameters"),
            blank=True,
            help_text=_("These parameters are added to proftpd.conf.")
            )

    class Meta:
        verbose_name = _("FTP")
        verbose_name_plural = _("FTP")


class TFTP(Model):
    tftp_directory = PathField(
            verbose_name=_("Directory"),
            help_text=_("The directory containing the files you want to "
                "publish. The remote host does not need to pass along the "
                "directory as part of the transfer."),
            )
    tftp_newfiles = models.BooleanField(
        verbose_name=_("Allow New Files"),
        default=False,
    )
    tftp_port = models.PositiveIntegerField(
            verbose_name=_("Port"),
            validators=[MinValueValidator(1), MaxValueValidator(65535)],
            default=69,
            help_text=_("The port to listen to. The default is to listen to "
                "the tftp port specified in /etc/services.")
            )
    tftp_username = UserField(
            max_length=120,
            default="nobody",
            verbose_name=_("Username"),
            help_text=_("Specifies the username which the service will run "
                "as.")
            )
    tftp_umask = models.CharField(
            max_length=120,
            verbose_name=_("umask"),
            default='022',
            help_text=_("Set the umask for newly created files to the "
                "specified value. The default is 022 (everyone can read, "
                "nobody can write).")
            )
    tftp_options = models.CharField(
            max_length=120,
            verbose_name=_("Extra options"),
            blank=True,
            help_text=_("Extra command line options (usually empty).")
            )

    class Meta:
        verbose_name = _("TFTP")
        verbose_name_plural = _("TFTP")

    class FreeAdmin:
        deletable = False
        icon_model = "TFTPIcon"


class SSH(Model):
    ssh_tcpport = models.PositiveIntegerField(
            verbose_name=_("TCP Port"),
            default=22,
            validators=[MinValueValidator(1), MaxValueValidator(65535)],
            help_text=_("Alternate TCP port. Default is 22"),
            )
    ssh_rootlogin = models.BooleanField(
        verbose_name=_("Login as Root with password"),
        help_text=_("Disabled: Root can only login via public key "
            "authentication; Enabled: Root login permitted with password"),
        default=False,
    )
    ssh_passwordauth = models.BooleanField(
        verbose_name=_("Allow Password Authentication"),
        default=False,
    )
    ssh_tcpfwd = models.BooleanField(
        verbose_name=_("Allow TCP Port Forwarding"),
        default=False,
    )
    ssh_compression = models.BooleanField(
        verbose_name=_("Compress Connections"),
        default=False,
    )
    ssh_privatekey = models.TextField(
            max_length=1024,
            verbose_name=_("Host Private Key"),
            blank=True,
            help_text=_("Paste a RSA PRIVATE KEY in PEM format here.")
            )
    ssh_sftp_log_level = models.CharField(
            verbose_name=_("SFTP Log Level"),
            choices=choices.SFTP_LOG_LEVEL,
            blank=True,
            max_length=20,
            help_text=_("Specifies which messages will be logged by "
                "sftp-server. INFO and VERBOSE log transactions that "
                "sftp-server performs on behalf of the client. DEBUG2 and "
                "DEBUG3 each specify higher levels of debugging output. The "
                "default is ERROR."),
            )
    ssh_sftp_log_facility = models.CharField(
            verbose_name=_("SFTP Log Facility"),
            choices=choices.SFTP_LOG_FACILITY,
            blank=True,
            max_length=20,
            help_text=_("Specifies the facility code that is used when "
                "logging messages from sftp-server."),
            )
    ssh_options = models.TextField(
            max_length=120,
            verbose_name=_("Extra options"),
            blank=True,
            help_text=_("Extra options to /etc/ssh/sshd_config (usually "
                "empty). Note, incorrect entered options prevent SSH service "
                "to be started.")
            )
    ssh_host_dsa_key = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_dsa_key_pub = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_ecdsa_key = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_ecdsa_key_pub = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_key = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_key_pub = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_rsa_key = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )
    ssh_host_rsa_key_pub = models.TextField(
            max_length=1024,
            editable=False,
            blank=True,
            null=True
            )

    class Meta:
        verbose_name = _("SSH")
        verbose_name_plural = _("SSH")

    class FreeAdmin:
        deletable = False
        icon_model = "OpenSSHIcon"
        advanced_fields = (
            'ssh_sftp_log_level',
            'ssh_sftp_log_facility',
            'ssh_privatekey',
            'ssh_options',
        )


class LLDP(Model):
    lldp_intdesc = models.BooleanField(
        verbose_name=_('Interface Description'),
        default=True,
        help_text=_('Save received info in interface description / alias'),
    )
    lldp_country = models.CharField(
        verbose_name=_('Country Code'),
        max_length=2,
        help_text=_(
            'Specify a two-letterISO 3166 country code (required for LLDP'
            'location support)'
        ),
        blank=True,
    )
    lldp_location = models.CharField(
        verbose_name=_('Location'),
        max_length=200,
        help_text=_('Specify the physical location of the host'),
        blank=True,
    )

    class Meta:
        verbose_name = _("LLDP")
        verbose_name_plural = _("LLDP")

    class FreeAdmin:
        deletable = False
        icon_model = "LLDPIcon"


class Rsyncd(Model):
    rsyncd_port = models.IntegerField(
            default=873,
            verbose_name=_("TCP Port"),
            help_text=_("Alternate TCP port. Default is 873"),
            )
    rsyncd_auxiliary = models.TextField(
            blank=True,
            verbose_name=_("Auxiliary parameters"),
            help_text=_("These parameters will be added to [global] settings "
                "in rsyncd.conf"),
            )

    class Meta:
        verbose_name = _("Configure Rsyncd")
        verbose_name_plural = _("Configure Rsyncd")

    class FreeAdmin:
        deletable = False
        menu_child_of = "services.Rsync"
        icon_model = u"rsyncdIcon"


class RsyncMod(Model):
    rsyncmod_name = models.CharField(
            max_length=120,
            verbose_name=_("Module name"),
            )
    rsyncmod_comment = models.CharField(
            max_length=120,
            blank=True,
            verbose_name=_("Comment"),
            )
    rsyncmod_path = PathField(
            verbose_name=_("Path"),
            help_text=_("Path to be shared"),
            )
    rsyncmod_mode = models.CharField(
            max_length=120,
            choices=choices.ACCESS_MODE,
            default="rw",
            verbose_name=_("Access Mode"),
            help_text=_("This controls the access a remote host has to this "
                "module"),
            )
    rsyncmod_maxconn = models.IntegerField(
            default=0,
            verbose_name=_("Maximum connections"),
            help_text=_("Maximum number of simultaneous connections. Default "
                "is 0 (unlimited)"),
            )
    rsyncmod_user = UserField(
        max_length=120,
        default="nobody",
        verbose_name=_("User"),
        help_text=_("This option specifies the user name that file "
            "transfers to and from that module should take place. In "
            "combination with the 'Group' option this determines what file"
            " permissions are available. Leave this field empty to use "
            "default settings"),
    )
    rsyncmod_group = GroupField(
        max_length=120,
        default="nobody",
        verbose_name=_("Group"),
        help_text=_("This option specifies the group name that file "
            "transfers to and from that module should take place. Leave "
            "this field empty to use default settings"),
    )
    rsyncmod_hostsallow = models.TextField(
            verbose_name=_("Hosts allow"),
            help_text=_("This option is a comma, space, or tab delimited set "
                "of hosts which are permitted to access this module. You can "
                "specify the hosts by name or IP number. Leave this field "
                "empty to use default settings"),
            blank=True,
            )
    rsyncmod_hostsdeny = models.TextField(
            verbose_name=_("Hosts deny"),
            help_text=_("This option is a comma, space, or tab delimited set "
                "of host which are NOT permitted to access this module. Where "
                "the lists conflict, the allow list takes precedence. In the "
                "event that it is necessary to deny all by default, use the "
                "keyword ALL (or the netmask 0.0.0.0/0) and then explicitly "
                "specify to the hosts allow parameter those hosts that should "
                "be permitted access. Leave this field empty to use default "
                "settings"),
            blank=True,
            )
    rsyncmod_auxiliary = models.TextField(
            verbose_name=_("Auxiliary parameters"),
            help_text=_("These parameters will be added to the module "
                "configuration in rsyncd.conf"),
            blank=True,
            )

    class Meta:
        verbose_name = _("Rsync Module")
        verbose_name_plural = _("Rsync Modules")
        ordering = ["rsyncmod_name"]

    class FreeAdmin:
        menu_child_of = 'services.Rsync'
        icon_model = u"rsyncModIcon"

    def __unicode__(self):
        return unicode(self.rsyncmod_name)


class SMART(Model):
    smart_interval = models.IntegerField(
            default=30,
            verbose_name=_("Check interval"),
            help_text=_("Sets the interval between disk checks to N minutes. "
                "The default is 30 minutes"),
            )
    smart_powermode = models.CharField(
            choices=choices.SMART_POWERMODE,
            default="never",
            max_length=60,
            verbose_name=_("Power mode"),
            )
    smart_difference = models.IntegerField(
            default=0,
            verbose_name=_("Difference"),
            help_text=_("Report if the temperature had changed by at least N "
                "degrees Celsius since last report. 0 to disable"),
            )
    smart_informational = models.IntegerField(
        default=0,
        verbose_name=_("Informational"),
        help_text=_("Report as informational in the system log if the "
            "temperature is greater or equal than N degrees Celsius. 0 to "
            "disable"),
    )
    smart_critical = models.IntegerField(
        default=0,
        verbose_name=_("Critical"),
        help_text=_("Report as critical in the system log and send an "
        "email if the temperature is greater or equal than N degrees "
        "Celsius. 0 to disable"),
    )
    smart_email = models.CharField(
            verbose_name=_("Email to report"),
            max_length=255,
            blank=True,
            help_text=_("Destination email address. Separate email addresses "
                "by commas"),
            )

    class Meta:
        verbose_name = _("S.M.A.R.T.")
        verbose_name_plural = _("S.M.A.R.T.")

    class FreeAdmin:
        deletable = False
        icon_model = u"SMARTIcon"


class RPCToken(Model):

    key = models.CharField(max_length=1024)
    secret = models.CharField(max_length=1024)

    @classmethod
    def new(cls):
        key = str(uuid.uuid4())
        h = hmac.HMAC(key=key, digestmod=hashlib.sha512)
        secret = str(h.hexdigest())
        instance = cls.objects.create(
            key=key,
            secret=secret,
            )
        return instance


class DomainController(Model):
    dc_realm = models.CharField(
            max_length=120,
            verbose_name=_("Realm"),
            help_text=_("Realm Name, eg EXAMPLE.ORG"),
            )
    dc_domain = models.CharField(
            max_length=120,
            verbose_name=_("Domain"),
            help_text=_("Domain Name in old format, eg EXAMPLE"),
            )
    dc_role = models.CharField(
            max_length=120,
            verbose_name=_("Server Role"),
            help_text=_("Server Role"),
            choices=choices.SAMBA4_ROLE_CHOICES,
            default='dc'
            )
    dc_dns_backend = models.CharField(
            max_length=120,
            verbose_name=_("DNS Backend"),
            help_text=_("DNS Backend, eg SAMBA_INTERNAL"),
            choices=choices.SAMBA4_DNS_BACKEND_CHOICES,  
            default='SAMBA_INTERNAL'
            )
    dc_dns_forwarder = models.CharField(
            max_length=120,
            verbose_name=_("DNS Forwarder"),
            help_text=_("DNS Forwarder IP Address"),
            )
    dc_forest_level = models.CharField(
            max_length=120,
            verbose_name=_("Domain Forest Level"),
            help_text=_("Domain and Forest Level, eg 2003"),
            choices=choices.SAMBA4_FOREST_LEVEL_CHOICES,
            default='2003'
            )
    dc_passwd = models.CharField(
            max_length=120,
            verbose_name=_("Administrator Password"),
            help_text=_("Administrator Password"),
            )
    dc_kerberos_realm = models.ForeignKey(
            KerberosRealm,
            verbose_name=_("Kerberos Realm"),
            on_delete=models.SET_NULL,
            blank=True,
            null=True
            )

    def __init__(self, *args, **kwargs):
        super(DomainController, self).__init__(*args, **kwargs)
        self.svc = 'domaincontroller'

    def save(self):
        super(DomainController, self).save()

        if not self.dc_kerberos_realm:
            try:
                from freenasUI.common.system import get_dc_hostname 

                dc_hostname = get_dc_hostname()
                kr = KerberosRealm() 
                kr.krb_realm = self.dc_realm.upper()
                kr.krb_kdc = dc_hostname
                kr.krb_admin_server = dc_hostname
                kr.krb_kpasswd_server = dc_hostname
                kr.save()

                self.dc_kerberos_realm = kr
                super(DomainController, self).save()

            except Exception as e:
                log.debug("DomainController: Unable to create kerberos realm: %s", e)

    class Meta:
        verbose_name = _(u"Domain Controller")
        verbose_name_plural = _(u"Domain Controller")

    class FreeAdmin:
        deletable = False
        icon_model = u"DomainControllerIcon"

class WebDAV(Model):
    webdav_protocol = models.CharField(
        max_length=120,
        choices=choices.PROTOCOL_CHOICES,
        default="http",
        verbose_name=_("Protocol")
    )

    webdav_tcpport = models.PositiveIntegerField(
        verbose_name=_("HTTP Port"),
        default=8080,
        validators=[MinValueValidator(1),MaxValueValidator(65535)],
        help_text=_("This is the port at which WebDAV will run on."
            "<br>Do not use a port that is already in use by another service (e.g. 22 for SSH)."),
    )

    webdav_tcpportssl =  models.PositiveIntegerField(
        verbose_name=_("HTTPS Port"),
        default=8081,
        validators=[MinValueValidator(1),MaxValueValidator(65535)],
        help_text=_("This is the port at which Secure WebDAV will run on."
            "<br>Do not use a port that is already in use by another service (e.g. 22 for SSH)."),
    )

    webdav_password = models.CharField(
        max_length=120,
        verbose_name=_("Webdav Password"),
        default="davtest",
        help_text=_("The Default Password is: davtest"),
    )

    webdav_htauth = models.CharField(
        max_length=120,
        verbose_name=_("HTTP Authentication"),
        choices=choices.HTAUTH_CHOICES,
        default='digest',
        help_text=_("Type of HTTP Authentication for WebDAV"
            "<br>Basic Auth: Password is sent over the network as plaintext (Avoid if HTTPS is disabled)"
            "<br>Digest Auth: Hash of the password is sent over the network (more secure)")
        )

    webdav_certssl = models.ForeignKey(
        Certificate,
        verbose_name=_("Webdav SSL Certificate"),
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _(u"WebDAV")
        verbose_name_plural = _(u"WebDAV")

    class FreeAdmin:
        deletable = False
        icon_model = u"WebDAVShareIcon"
