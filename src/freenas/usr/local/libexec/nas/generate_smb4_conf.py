#!/usr/local/bin/python

import os
import re
import sys
import string
import tempfile
import time

from dns import resolver

sys.path.extend([
    '/usr/local/www',
    '/usr/local/www/freenasUI'
])

os.environ["DJANGO_SETTINGS_MODULE"] = "freenasUI.settings"

# Make sure to load all modules
from django.db.models.loading import cache
cache.get_apps()

from django.db.models import Q

from freenasUI.account.models import (
    bsdUsers,
    bsdGroups,
    bsdGroupMembership
)
from freenasUI.common.freenasldap import (
    FreeNAS_ActiveDirectory,
    FreeNAS_LDAP,
    FLAGS_DBINIT
)
from freenasUI.common.pipesubr import pipeopen
from freenasUI.common.samba import Samba4
from freenasUI.common.system import (
    activedirectory_enabled,
    domaincontroller_enabled,
    ldap_enabled,
    ldap_has_samba_schema,
    nt4_enabled
)
from freenasUI.directoryservice.models import (
    ActiveDirectory,
    LDAP,
    NT4,
    idmap_ad,
    idmap_adex,
    idmap_autorid,
    idmap_hash,
    idmap_ldap,
    idmap_nss,
    idmap_rfc2307,
    idmap_rid,
    idmap_tdb,
    idmap_tdb2,
    IDMAP_TYPE_NONE,
    IDMAP_TYPE_AD,
    IDMAP_TYPE_ADEX,
    IDMAP_TYPE_AUTORID,
    IDMAP_TYPE_HASH,
    IDMAP_TYPE_LDAP,
    IDMAP_TYPE_NSS,
    IDMAP_TYPE_RFC2307,
    IDMAP_TYPE_RID,
    IDMAP_TYPE_TDB,
    IDMAP_TYPE_TDB2,
    DS_TYPE_CIFS
)
from freenasUI.directoryservice.utils import get_idmap_object
from freenasUI.middleware.notifier import notifier

from freenasUI.services.models import (
    CIFS,
    DomainController
)
from freenasUI.sharing.models import CIFS_Share
from freenasUI.storage.models import Task

def smb4_ldap_enabled():
    ret = False

    if ldap_enabled() and ldap_has_samba_schema():
        ret = True

    return ret

def is_within_zfs(mountpoint):
    try:
        st = os.stat(mountpoint)
    except:
        return False

    share_dev = st.st_dev
    p = pipeopen("zfs list -H -o mountpoint")
    zfsout = p.communicate()
    if p.returncode != 0:
        return False
    if zfsout:
        zfsout = zfsout[0]

    for mp in zfsout.split('\n'):
        mp = mp.strip()
        if mp == '-':
            continue

        try:
            st = os.stat(mp)
        except:
            continue

        if st.st_dev == share_dev:
            return True

    return False


def get_sysctl(name):
    p = pipeopen("/sbin/sysctl -n '%s'" % name)
    out = p.communicate()
    if p.returncode != 0:
        return None
    try:
        out = out[0].strip()
    except:
        pass
    return out


def get_server_services():
    server_services = [
        'rpc', 'nbt', 'wrepl', 'ldap', 'cldap', 'kdc', 'drepl', 'winbind',
        'ntp_signd', 'kcc', 'dnsupdate', 'dns', 'smb'
    ]
    return server_services


def get_dcerpc_endpoint_servers():
    dcerpc_endpoint_servers = [
        'epmapper', 'wkssvc', 'rpcecho', 'samr', 'netlogon', 'lsarpc',
        'spoolss', 'drsuapi', 'dssetup', 'unixinfo', 'browser', 'eventlog6',
        'backupkey', 'dnsserver', 'winreg', 'srvsvc'
    ]
    return dcerpc_endpoint_servers


def get_server_role():
    role = "standalone"
    if nt4_enabled() or activedirectory_enabled() or smb4_ldap_enabled():
        role = "member"

    if domaincontroller_enabled():
        try:
            dc = DomainController.objects.all()[0]
            role = dc.dc_role
        except:
            pass

    return role


def confset1(conf, line, space=4):
    if not line:
        return

    i = 0
    str = ''
    while i < space:
        str += ' '
        i += 1
    line = str + line

    conf.append(line)


def confset2(conf, line, var, space=4):
    if not line:
        return

    i = 0
    str = ''
    while i < space:
        str += ' '
        i += 1
    line = str + line

    if var:
        conf.append(line % var)


def configure_idmap_ad(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_ad_range_low,
        idmap.idmap_ad_range_high
    ))
    confset1(smb4_conf, "idmap config %s: schema mode = %s" % (
        domain,
        idmap.idmap_ad_schema_mode
    ))


def configure_idmap_adex(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap backend = adex")
    confset1(smb4_conf, "idmap uid = %d-%d" % (
        domain,
        idmap.idmap_adex_range_low,
        idmap.idmap_adex_range_high
    ))
    confset1(smb4_conf, "idmap gid = %d-%d" % (
        domain,
        idmap.idmap_adex_range_low,
        idmap.idmap_adex_range_high
    ))


def configure_idmap_autorid(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_autorid_range_low,
        idmap.idmap_autorid_range_high
    ))
    confset1(smb4_conf, "idmap config %s: rangesize = %d" % (
        domain,
        idmap.idmap_autorid_rangesize
    ))
    confset1(smb4_conf, "idmap config %s: read only = %s" % (
        domain,
        "yes" if idmap.idmap_autorid_readonly else "no"
    ))
    confset1(smb4_conf, "idmap config %s: ignore builtin = %s" % (
        domain,
        "yes" if idmap.idmap_autorid_ignore_builtin else "no"
    ))


def configure_idmap_hash(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_hash_range_low,
        idmap.idmap_hash_range_high
    ))
    confset1(smb4_conf, "idmap_hash: name_map = %s" %
        idmap.idmap_hash_range_name_map)


def configure_idmap_ldap(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_ldap_range_low,
        idmap.idmap_ldap_range_high
    ))
    if idmap.idmap_ldap_ldap_base_dn:
        confset1(smb4_conf, "idmap config %s: ldap base dn = %s" % (
            domain,
            idmap.idmap_ldap_ldap_base_dn
        ))
    if idmap.idmap_ldap_ldap_user_dn:
        confset1(smb4_conf, "idmap config %s: ldap user dn = %s" % (
            domain,
            idmap.idmap_ldap_ldap_user_dn
        ))
    if idmap.idmap_ldap_ldap_url:
        confset1(smb4_conf, "idmap config %s: ldap url = %s" % (
            domain,
            idmap.idmap_ldap_ldap_url
        ))


def configure_idmap_nss(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_nss_range_low,
        idmap.idmap_nss_range_high
    ))


def configure_idmap_rfc2307(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_rfc2307_range_low,
        idmap.idmap_rfc2307_range_high
    ))
    confset1(smb4_conf, "idmap config %s: ldap_server = %s" % (
        domain,
        idmap.idmap_rfc2307_ldap_server
    ))
    confset1(smb4_conf, "idmap config %s: bind_path_user = %s" % (
        domain,
        idmap.idmap_rfc2307_bind_path_user
    ))
    confset1(smb4_conf, "idmap config %s: bind_path_group = %s" % (
        domain,
        idmap.idmap_rfc2307_bind_path_group
    ))
    confset1(smb4_conf, "idmap config %s: user_cn = %s" % (
        domain,
        "yes" if idmap.idmap_rfc2307_user_cn else "no"
    ))
    confset1(smb4_conf, "idmap config %s: cn_realm = %s" % (
        domain,
        "yes" if idmap.idmap_rfc2307_cn_realm else "no"
    ))
    if idmap.idmap_rfc2307_ldap_domain:
        confset1(smb4_conf, "idmap config %s: ldap_domain = %s" % (
            domain,
            idmap.idmap_rfc2307_ldap_domain
        ))
    if idmap.idmap_rfc2307_ldap_url:
        confset1(smb4_conf, "idmap config %s: ldap_url = %s" % (
            domain,
            idmap.idmap_rfc2307_ldap_url
        ))
    if idmap.idmap_rfc2307_ldap_user_dn:
        confset1(smb4_conf, "idmap config %s: ldap_user_dn = %s" % (
            domain,
            idmap.idmap_rfc2307_ldap_user_dn
        ))
    if idmap.idmap_rfc2307_ldap_realm:
        confset1(smb4_conf, "idmap config %s: ldap_realm = %s" % (
            domain,
            idmap.idmap_rfc2307_ldap_realm
        ))


def configure_idmap_rid(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_rid_range_low,
        idmap.idmap_rid_range_high
    ))


def configure_idmap_tdb(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_tdb_range_low,
        idmap.idmap_tdb_range_high
    ))

 
def configure_idmap_tdb2(smb4_conf, idmap, domain):
    confset1(smb4_conf, "idmap config %s: backend = %s" % (
        domain,
        idmap.idmap_backend_name
    ))
    confset1(smb4_conf, "idmap config %s: range = %d-%d" % (
        domain,
        idmap.idmap_tdb2_range_low,
        idmap.idmap_tdb2_range_high
    ))
    confset2(smb4_conf, "idmap config %s: script = %s" % (
        domain,
        idmap.idmap_tdb2_script
    ))


def configure_idmap_backend(smb4_conf, idmap, domain):
    idmap_functions = {
        IDMAP_TYPE_AD: configure_idmap_ad,
        IDMAP_TYPE_ADEX: configure_idmap_ad,
        IDMAP_TYPE_AUTORID: configure_idmap_autorid,
        IDMAP_TYPE_HASH: configure_idmap_hash,
        IDMAP_TYPE_LDAP: configure_idmap_ldap,
        IDMAP_TYPE_NSS: configure_idmap_nss,
        IDMAP_TYPE_RFC2307: configure_idmap_rfc2307,
        IDMAP_TYPE_RID: configure_idmap_rid,
        IDMAP_TYPE_TDB: configure_idmap_tdb,
        IDMAP_TYPE_TDB2: configure_idmap_tdb2
    }

    if not domain:
        domain = "*"

    try:
        func = idmap_functions[idmap.idmap_backend_type]
        func(smb4_conf, idmap, domain)

    except:
        pass 


def add_nt4_conf(smb4_conf):
    rid_range_start = 20000
    rid_range_end = 20000000

    try:
        nt4 = NT4.objects.all()[0]
    except:
        return

    try:
        answers = resolver.query(nt4.nt4_dcname, 'A')
        dc_ip = answers[0]

    except Exception as e:
        dc_ip = nt4.nt4_dcname

    with open("/usr/local/etc/lmhosts", "w") as f:
        f.write("%s\t%s\n" % (dc_ip, nt4.nt4_workgroup.upper()))
        f.close()

    nt4_workgroup = nt4.nt4_workgroup.upper()

    confset2(smb4_conf, "netbios name = %s", nt4.nt4_netbiosname.upper())
    confset2(smb4_conf, "workgroup = %s", nt4_workgroup)

    confset1(smb4_conf, "security = domain")
    confset1(smb4_conf, "password server = *")

    idmap = get_idmap_object(nt4.ds_type, nt4.id, nt4.nt4_idmap_backend)
    configure_idmap_backend(smb4_conf, idmap, nt4_workgroup)

    confset1(smb4_conf, "winbind cache time = 7200")
    confset1(smb4_conf, "winbind offline logon = yes")
    confset1(smb4_conf, "winbind enum users = yes")
    confset1(smb4_conf, "winbind enum groups = yes")
    confset1(smb4_conf, "winbind nested groups = yes")
    confset2(smb4_conf, "winbind use default domain = %s",
        "yes" if nt4.nt4_use_default_domain else "no")

    confset1(smb4_conf, "template shell = /bin/sh")

    confset1(smb4_conf, "local master = no")
    confset1(smb4_conf, "domain master = no")
    confset1(smb4_conf, "preferred master = no")


def set_ldap_password():
    try:
        ldap = LDAP.objects.all()[0]
    except:
        return

    if ldap.ldap_bindpw:
        p = pipeopen("/usr/local/bin/smbpasswd -w '%s'" % ldap.ldap_bindpw)
        out = p.communicate()
        if out and out[1]:
            for line in out[1].split('\n'):
                print line


def add_ldap_conf(smb4_conf):
    try:
        ldap = LDAP.objects.all()[0]
        cifs = CIFS.objects.all()[0]
    except:
        return

    confset1(smb4_conf, "security = user")

    confset1(
        smb4_conf,
        "passdb backend = ldapsam:%s://%s" % (
            "ldaps" if ldap.ldap_ssl == 'on' else "ldap",
            ldap.ldap_hostname
        )
    )

    ldap_workgroup = cifs.cifs_srv_workgroup.upper()

    confset2(smb4_conf, "ldap admin dn = %s", ldap.ldap_binddn)
    confset2(smb4_conf, "ldap suffix = %s", ldap.ldap_basedn)
    confset2(smb4_conf, "ldap user suffix = %s", ldap.ldap_usersuffix)
    confset2(smb4_conf, "ldap group suffix = %s", ldap.ldap_groupsuffix)
    confset2(smb4_conf, "ldap machine suffix = %s", ldap.ldap_machinesuffix)
    confset2(
        smb4_conf,
        "ldap ssl = %s",
        "start tls" if (ldap.ldap_ssl == 'start_tls') else 'off'
    )

    confset1(smb4_conf, "ldap replication sleep = 1000")
    confset1(smb4_conf, "ldap passwd sync = yes")
    confset1(smb4_conf, "ldapsam:trusted = yes")

    confset2(smb4_conf, "netbios name = %s", cifs.cifs_srv_netbiosname.upper())
    confset2(smb4_conf, "workgroup = %s", ldap_workgroup)
    confset1(smb4_conf, "domain logons = yes")

    idmap = get_idmap_object(ldap.ds_type, ldap.id, ldap.ldap_idmap_backend)
    configure_idmap_backend(smb4_conf, idmap, ldap_workgroup)


def add_activedirectory_conf(smb4_conf):
    rid_range_start = 20000
    rid_range_end = 20000000

    ad_range_start = 10000
    ad_range_end = 90000000

    try:
        ad = ActiveDirectory.objects.all()[0]
    except:
        return

    cachedir = "/var/tmp/.cache/.samba"

    try:
        os.makedirs(cachedir)
    except:
        pass

    ad_workgroup = None
    try:
        fad = FreeNAS_ActiveDirectory(flags=FLAGS_DBINIT)
        ad_workgroup = fad.netbiosname.upper()
    except:
        return

    confset2(smb4_conf, "netbios name = %s", ad.ad_netbiosname.upper())
    confset2(smb4_conf, "workgroup = %s", ad_workgroup)
    confset2(smb4_conf, "realm = %s", ad.ad_domainname.upper())
    confset1(smb4_conf, "security = ADS")
    confset1(smb4_conf, "client use spnego = yes")
    confset2(smb4_conf, "cache directory = %s", cachedir)

    confset1(smb4_conf, "local master = no")
    confset1(smb4_conf, "domain master = no")
    confset1(smb4_conf, "preferred master = no")

    confset1(smb4_conf, "winbind cache time = 7200")
    confset1(smb4_conf, "winbind offline logon = yes")
    confset1(smb4_conf, "winbind enum users = yes")
    confset1(smb4_conf, "winbind enum groups = yes")
    confset1(smb4_conf, "winbind nested groups = yes")
    confset2(smb4_conf, "winbind use default domain = %s",
        "yes" if ad.ad_use_default_domain else "no")
    confset1(smb4_conf, "winbind refresh tickets = yes")

    if ad.ad_nss_info:
        confset2(smb4_conf, "winbind nss info = %s", ad.ad_nss_info)

    idmap = get_idmap_object(ad.ds_type, ad.id, ad.ad_idmap_backend)
    configure_idmap_backend(smb4_conf, idmap, ad_workgroup)

    confset2(smb4_conf, "allow trusted domains = %s",
        "yes" if ad.ad_allow_trusted_doms else "no")

    confset2(smb4_conf, "client ldap sasl wrapping = %s",
        ad.ad_ldap_sasl_wrapping)

    confset1(smb4_conf, "template shell = /bin/sh")
    confset2(smb4_conf, "template homedir = %s",
        "/home/%D/%U" if not ad.ad_use_default_domain else "/home/%U")


def add_domaincontroller_conf(smb4_conf):
    try:
        dc = DomainController.objects.all()[0]
        cifs = CIFS.objects.all()[0]
    except:
        return

    #server_services = get_server_services()
    #dcerpc_endpoint_servers = get_dcerpc_endpoint_servers()

    confset2(smb4_conf, "netbios name = %s", cifs.cifs_srv_netbiosname.upper())
    confset2(smb4_conf, "workgroup = %s", dc.dc_domain.upper())
    confset2(smb4_conf, "realm = %s", dc.dc_realm)
    confset2(smb4_conf, "dns forwarder = %s", dc.dc_dns_forwarder)
    confset1(smb4_conf, "idmap_ldb:use rfc2307 = yes")

    #confset2(smb4_conf, "server services = %s",
    #    string.join(server_services, ',').rstrip(','))
    #confset2(smb4_conf, "dcerpc endpoint servers = %s",
    #    string.join(dcerpc_endpoint_servers, ',').rstrip(','))


def generate_smb4_tdb(smb4_tdb):
    try:
        users = bsdUsers.objects.filter(bsdusr_smbhash__regex=r'^.+:.+:XXXX.+$',
            bsdusr_locked=0, bsdusr_password_disabled=0)
        for u in users:
            smb4_tdb.append(u.bsdusr_smbhash)
    except:
        return


def generate_smb4_conf(smb4_conf, role):
    try:
        cifs = CIFS.objects.all()[0]
    except:
        return

    if not cifs.cifs_srv_guest:
        cifs.cifs_srv_guest = 'ftp'
    if not cifs.cifs_srv_filemask:
        cifs.cifs_srv_filemask = "0666"
    if not cifs.cifs_srv_dirmask:
        cifs.cifs_srv_dirmask = "0777"

    # standard stuff... should probably do this differently
    confset1(smb4_conf, "[global]", space=0)

    confset2(smb4_conf, "server min protocol = %s", cifs.cifs_srv_min_protocol)
    confset2(smb4_conf, "server max protocol = %s", cifs.cifs_srv_max_protocol)
    if cifs.cifs_srv_bindip:
        interfaces = cifs.cifs_srv_bindip.replace(',', ' ')
        if role != 'dc':
            interfaces = "127.0.0.1 %s" % interfaces
        confset2(smb4_conf, "interfaces = %s", interfaces)
        confset1(smb4_conf, "bind interfaces only = yes")

    confset1(smb4_conf, "encrypt passwords = yes")
    confset1(smb4_conf, "dns proxy = no")
    confset1(smb4_conf, "strict locking = no")
    confset1(smb4_conf, "oplocks = yes")
    confset1(smb4_conf, "deadtime = 15")
    confset1(smb4_conf, "max log size = 51200")

    confset2(smb4_conf, "max open files = %d", long(get_sysctl('kern.maxfilesperproc')) - 25)

    if cifs.cifs_srv_syslog:
        confset1(smb4_conf, "syslog only = yes")
        confset1(smb4_conf, "syslog = 1")

    confset1(smb4_conf, "load printers = no")
    confset1(smb4_conf, "printing = bsd")
    confset1(smb4_conf, "printcap name = /dev/null")
    confset1(smb4_conf, "disable spoolss = yes")
    confset1(smb4_conf, "getwd cache = yes")
    confset2(smb4_conf, "guest account = %s", cifs.cifs_srv_guest.encode('utf8'))
    confset1(smb4_conf, "map to guest = Bad User")
    confset2(smb4_conf, "obey pam restrictions = %s",
        "yes" if cifs.cifs_srv_obey_pam_restrictions else "no")
    confset1(smb4_conf, "directory name cache size = 0")
    confset1(smb4_conf, "kernel change notify = no")

    confset1(smb4_conf, "panic action = /usr/local/libexec/samba/samba-backtrace")

    confset2(smb4_conf, "server string = %s", cifs.cifs_srv_description)
    confset1(smb4_conf, "ea support = yes")
    confset1(smb4_conf, "store dos attributes = yes")
    confset2(smb4_conf, "hostname lookups = %s",
        "yes" if cifs.cifs_srv_hostlookup else False)
    confset2(smb4_conf, "unix extensions = %s",
        "no" if not cifs.cifs_srv_unixext else False)
    confset2(smb4_conf, "time server = %s",
        "yes" if cifs.cifs_srv_timeserver else False)
    confset2(smb4_conf, "null passwords = %s",
        "yes" if cifs.cifs_srv_nullpw else False)
    confset2(smb4_conf, "acl allow execute always = %s",
        "true" if cifs.cifs_srv_allow_execute_always else "false")
    confset1(smb4_conf, "acl check permissions = true")
    confset1(smb4_conf, "dos filemode = yes")

    if not smb4_ldap_enabled():
        confset2(smb4_conf, "domain logons = %s",
            "yes" if cifs.cifs_srv_domain_logons else "no")

    if cifs.cifs_srv_localmaster and not nt4_enabled() \
        and not activedirectory_enabled():
        confset2(smb4_conf, "local master = %s",
            "yes" if cifs.cifs_srv_localmaster else False)

    idmap = get_idmap_object(DS_TYPE_CIFS, cifs.id, 'tdb')
    configure_idmap_backend(smb4_conf, idmap, None)

    if role == 'auto':
        confset1(smb4_conf, "server role = auto")

    elif role == 'classic':
        confset1(smb4_conf, "server role = classic primary domain controller")

    elif role == 'netbios':
        confset1(smb4_conf, "server role = netbios backup domain controller")

    elif role == 'dc':
        confset1(smb4_conf, "server role = active directory domain controller")
        add_domaincontroller_conf(smb4_conf)

    elif role == 'member':
        confset1(smb4_conf, "server role = member server")

        if nt4_enabled():
            add_nt4_conf(smb4_conf)

        elif smb4_ldap_enabled():
            add_ldap_conf(smb4_conf)

        elif activedirectory_enabled():
            add_activedirectory_conf(smb4_conf)

    elif role == 'standalone':
        confset1(smb4_conf, "server role = standalone")
        confset2(smb4_conf, "netbios name = %s", cifs.cifs_srv_netbiosname.upper())
        confset2(smb4_conf, "workgroup = %s", cifs.cifs_srv_workgroup.upper())
        confset1(smb4_conf, "security = user")

    if role != 'dc':
        confset1(smb4_conf, "pid directory = /var/run/samba")
        confset1(smb4_conf, "smb passwd file = /var/etc/private/smbpasswd")
        confset1(smb4_conf, "private dir = /var/etc/private")

    confset2(smb4_conf, "create mask = %s", cifs.cifs_srv_filemask)
    confset2(smb4_conf, "directory mask = %s", cifs.cifs_srv_dirmask)
    confset1(smb4_conf, "client ntlmv2 auth = yes")
    confset2(smb4_conf, "dos charset = %s", cifs.cifs_srv_doscharset)
    confset2(smb4_conf, "unix charset = %s", cifs.cifs_srv_unixcharset)

    if cifs.cifs_srv_loglevel and cifs.cifs_srv_loglevel is not True:
        confset2(smb4_conf, "log level = %s", cifs.cifs_srv_loglevel)

    for line in cifs.cifs_srv_smb_options.split('\n'):
        confset1(smb4_conf, line)


def generate_smb4_shares(smb4_shares):
    try:
        shares = CIFS_Share.objects.all()
    except:
        return

    if len(shares) == 0:
        return

    p = pipeopen("zfs list -H -o mountpoint,name")
    zfsout = p.communicate()[0].split('\n')
    if p.returncode != 0:
        zfsout = []

    for share in shares:
        if not os.path.isdir(share.cifs_path.encode('utf8')) and not share.cifs_home:
            continue

        task = False
        for line in zfsout:
            try:
                zfs_mp, zfs_ds = line.split()
                if share.cifs_path == zfs_mp or share.cifs_path.startswith("%s/" % zfs_mp):
                    if share.cifs_path == zfs_mp:
                        task = Task.objects.filter(task_filesystem = zfs_ds)[0]
                    else:
                        task = Task.objects.filter(Q(task_filesystem = zfs_ds) & Q(task_recursive=True))[0]
                    break
            except:
                pass

        confset1(smb4_shares, "\n")
        if share.cifs_home:
            confset1(smb4_shares, "[homes]", space=0)

            valid_users_path = "%U"
            valid_users = "%U"

            if activedirectory_enabled():
                try:
                    ad = ActiveDirectory.objects.all()[0]
                    if not ad.ad_use_default_domain:
                        valid_users_path = "%D/%U"
                        valid_users = "%D\%U"
                except:
                    pass

            confset2(smb4_shares, "valid users = %s", valid_users)

            if share.cifs_path:
                cifs_homedir_path = u"%s/%s" % (share.cifs_path, valid_users_path)
                confset2(smb4_shares, "path = %s", cifs_homedir_path.encode('utf8'))
            if share.cifs_comment:
                confset2(smb4_shares, "comment = %s", share.cifs_comment.encode('utf8'))
            else:
                confset1(smb4_shares, "comment = Home Directories")
        else:
            confset2(smb4_shares, "[%s]", share.cifs_name.encode('utf8'), space=0)
            confset2(smb4_shares, "path = %s", share.cifs_path.encode('utf8'))
            confset2(smb4_shares, "comment = %s", share.cifs_comment.encode('utf8'))
        confset1(smb4_shares, "printable = no")
        confset1(smb4_shares, "veto files = /.snapshot/.windows/.mac/.zfs/")
        confset2(smb4_shares, "writeable = %s",
            "no" if share.cifs_ro else "yes")
        confset2(smb4_shares, "browseable = %s",
            "yes" if share.cifs_browsable else "no")

        vfs_objects = []
        if share.cifs_recyclebin:
            vfs_objects.append('recycle')
        if task:
            vfs_objects.append('shadow_copy2')
        if is_within_zfs(share.cifs_path):
            vfs_objects.append('zfsacl')
        vfs_objects.extend(share.cifs_vfsobjects)

        confset1(smb4_shares, "recycle:repository = .recycle/%U")
        confset1(smb4_shares, "recycle:keeptree = yes")
        confset1(smb4_shares, "recycle:versions = yes")
        confset1(smb4_shares, "recycle:touch = yes")
        confset1(smb4_shares, "recycle:directory_mode = 0777")
        confset1(smb4_shares, "recycle:subdir_mode = 0700")

        if task:
            confset1(smb4_shares, "shadow:snapdir = .zfs/snapshot")
            confset1(smb4_shares, "shadow:sort = desc")
            confset1(smb4_shares, "shadow:localtime = yes")
            confset1(smb4_shares, "shadow:format = auto-%%Y%%m%%d.%%H%%M-%s%s" % (
                task.task_ret_count, task.task_ret_unit[0]))
        if vfs_objects:
            confset2(smb4_shares, "vfs objects = %s", ' '.join(vfs_objects).encode('utf8'))

        confset2(smb4_shares, "hide dot files = %s",
            "no" if share.cifs_showhiddenfiles else "yes")
        confset2(smb4_shares, "hosts allow = %s", share.cifs_hostsallow)
        confset2(smb4_shares, "hosts deny = %s", share.cifs_hostsdeny)
        confset2(smb4_shares, "guest ok = %s", "yes" if share.cifs_guestok else "no")

        confset2(smb4_shares, "guest only = %s",
            "yes" if share.cifs_guestonly else False)

        confset1(smb4_shares, "nfs4:mode = special")
        confset1(smb4_shares, "nfs4:acedup = merge")
        confset1(smb4_shares, "nfs4:chown = true")
        confset1(smb4_shares, "zfsacl:acesort = dontcare")

        for line in share.cifs_auxsmbconf.split('\n'):
            confset1(smb4_shares, line)


def provision_smb4():
    if not Samba4().domain_provision():
        print >> sys.stderr, "Failed to provision domain"
        return False

    if not Samba4().disable_password_complexity():
        print >> sys.stderr, "Failed to disable password complexity"
        return False

    if not Samba4().set_min_pwd_length():
        print >> sys.stderr, "Failed to set minimum password length"
        return False

    if not Samba4().set_administrator_password():
        print >> sys.stderr, "Failed to set administrator password"
        return False

    if not Samba4().sentinel_file_create():
        return False

    return True


def smb4_mkdir(dir):
    try:
        os.makedirs(dir)
    except:
        pass


def smb4_unlink(dir):
    try:
        os.unlink(dir)
    except:
        pass


def smb4_setup():
    statedir = "/var/db/samba4"

    smb4_mkdir("/var/run/samba")
    smb4_mkdir("/var/db/samba")

    smb4_mkdir("/var/run/samba4")

    smb4_mkdir("/var/log/samba4")
    os.chmod("/var/log/samba4", 0755)

    smb4_mkdir("/var/etc/private")
    os.chmod("/var/etc/private", 0700)

    smb4_unlink("/usr/local/etc/smb.conf")
    smb4_unlink("/usr/local/etc/smb4.conf")

    if hasattr(notifier, 'failover_status') and notifier().failover_status() == 'BACKUP':
        return

    systemdataset, volume, basename = notifier().system_dataset_settings()

    if not volume or not volume.is_decrypted():
        if os.path.islink(statedir):
            smb4_unlink(statedir)
            smb4_mkdir(statedir)
        return

    basename_realpath = os.path.join(notifier().system_dataset_path(), 'samba4')
    statedir_realpath = os.path.realpath(statedir)

    if os.path.islink(statedir) and not os.path.exists(statedir):
        smb4_unlink(statedir)

    if basename_realpath != statedir_realpath and os.path.exists(basename_realpath):
        smb4_unlink(statedir)
        if os.path.exists(statedir):
            olddir = "%s.%s" % (statedir, time.strftime("%Y%m%d%H%M%S"))
            try:
                os.rename(statedir, olddir)
            except Exception as e:
                print >> sys.stderr, "Unable to rename '%s' to '%s' (%s)" % (
                    statedir, olddir, e)
                sys.exit(1)

        try:
            os.symlink(basename_realpath, statedir)
        except Exception as e:
            print >> sys.stderr, "Unable to create symlink '%s' -> '%s' (%s)" % (
                basename_realpath, statedir, e)
            sys.exit(1)

    if os.path.islink(statedir) and not os.path.exists(statedir_realpath):
        smb4_unlink(statedir)
        smb4_mkdir(statedir)

    smb4_mkdir("/var/db/samba4/private")
    os.chmod("/var/db/samba4/private", 0700)


def get_old_samba4_datasets():
    old_samba4_datasets = []

    fsvols = notifier().list_zfs_fsvols()
    for fsvol in fsvols:
        if re.match('^.+/.samba4\/?$', fsvol):
            old_samba4_datasets.append(fsvol)

    return old_samba4_datasets


def migration_available(old_samba4_datasets):
    res = False

    if old_samba4_datasets and len(old_samba4_datasets) == 1:
        res = True
    elif old_samba4_datasets:
        with open("/var/db/samba4/.alert_cant_migrate", "w") as f:
            f.close()

    return res


def do_migration(old_samba4_datasets):
    if len(old_samba4_datasets) > 1:
        return False
    old_samba4_dataset = "/mnt/%s/" % old_samba4_datasets[0]

    try:
        pipeopen("/usr/local/bin/rsync -avz '%s'* '/var/db/samba4/'" % old_samba4_dataset).wait()
        notifier().destroy_zfs_dataset(old_samba4_datasets[0], True)

    except Exception as e:
        print >> sys.stderr, e
    
    return True


def smb4_import_users(smb_conf_path, importfile, exportfile=None):
    args = [
        "/usr/local/bin/pdbedit",
        "-d 0",
        "-i smbpasswd:%s" % importfile,
        "-s %s" % smb_conf_path
    ]

    if exportfile != None:
        args.append("-e %s" % exportfile)

    p = pipeopen(string.join(args, ' '))
    pdbedit_out = p.communicate()
    if pdbedit_out and pdbedit_out[0]:
        for line in pdbedit_out[0].split('\n'):
            print line


def smb4_grant_user_rights(user):
    args = [
        "/usr/local/bin/net",
        "sam",
        "rights",
        "grant"
    ]

    rights = [
        "SeTakeOwnershipPrivilege",
        "SeBackupPrivilege",
        "SeRestorePrivilege"
    ] 

    net_cmd = "%s %s %s" % (
        string.join(args, ' '),
        user,
        string.join(rights, ' ')
    )

    p = pipeopen(net_cmd)
    net_out = p.communicate()
    if net_out and net_out[0]:
        for line in net_out[0].split('\n'):
            print line 

    if p.returncode != 0:
        return False

    return True

    
def smb4_grant_rights():
    args = [
        "/usr/local/bin/pdbedit",
        "-L"
    ]

    p = pipeopen(string.join(args, ' '))
    pdbedit_out = p.communicate()
    if pdbedit_out and pdbedit_out[0]:
        for line in pdbedit_out[0].split('\n'):
            if not line:
                continue

            parts = line.split(':')
            user = parts[0]
            smb4_grant_user_rights(user)


def get_groups():
    _groups = {}

    groups = bsdGroups.objects.filter(bsdgrp_builtin=0)
    for g in groups:
        key = str(g.bsdgrp_group)
        _groups[key] = []
        members = bsdGroupMembership.objects.filter(bsdgrpmember_group=g.id)
        for m in members:
             u = bsdUsers.objects.filter(bsdusr_username=m.bsdgrpmember_user)
             if u:
                 u = u[0]
                 _groups[key].append(str(u.bsdusr_username))

    return _groups


def smb4_import_groups():
    s = Samba4()

    groups = get_groups()
    for g in groups:
        s.group_add(g)
        if groups[g]:
            s.group_addmembers(g, groups[g])


def smb4_group_mapped(groupmap, group):
    for gm in groupmap:
        unixgroup = gm['unixgroup']
        if group == unixgroup:
            return True

    return False


# Windows no likey
def smb4_groupname_is_username(group):
    cmd = "/usr/bin/getent passwd '%s'" % group

    p = pipeopen(cmd)
    p.communicate()
    if p.returncode == 0:
        return True

    return False


def smb4_map_groups():
    groupmap = notifier().groupmap_list()
    groups = get_groups()
    for g in groups:
        if not smb4_group_mapped(groupmap, g) and \
            not smb4_groupname_is_username(g):
            notifier().groupmap_add(unixgroup=g, ntgroup=g)


def main():
    smb_conf_path = "/usr/local/etc/smb4.conf"

    smb4_tdb = []
    smb4_conf = []
    smb4_shares = []

    smb4_setup()

    old_samba4_datasets = get_old_samba4_datasets()
    if migration_available(old_samba4_datasets):
        do_migration(old_samba4_datasets)

    role = get_server_role()

    generate_smb4_tdb(smb4_tdb)
    generate_smb4_conf(smb4_conf, role)
    generate_smb4_shares(smb4_shares)

    if role == 'dc' and not Samba4().domain_provisioned():
        provision_smb4()

    with open(smb_conf_path, "w") as f:
        for line in smb4_conf:
            f.write(line + '\n')
        for line in smb4_shares:
            f.write(line + '\n')
        f.close()

    if role == 'member' and smb4_ldap_enabled():
        set_ldap_password()

    (fd, tmpfile) = tempfile.mkstemp(dir="/tmp")
    for line in smb4_tdb:
        os.write(fd, line + '\n')
    os.close(fd)

    if role != 'dc':
        smb4_import_users(smb_conf_path, tmpfile,
            "tdbsam:/var/etc/private/passdb.tdb")
        smb4_map_groups()
        smb4_grant_rights()

    os.unlink(tmpfile)


if __name__ == '__main__':
    main()
