import glob
import os
import pwd
import subprocess

from django.utils.translation import ugettext_lazy as _

from dojango import forms
from freenasUI import choices
from freenasUI.common.forms import ModelForm, mchoicefield
from freenasUI.freeadmin.forms import CronMultiple
from freenasUI.middleware.notifier import notifier
from freenasUI.tasks import models


class CronJobForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.CronJob
        widgets = {
            'cron_command': forms.widgets.TextInput(),
            'cron_minute': CronMultiple(
                attrs={'numChoices': 60, 'label': _("minute")}
            ),
            'cron_hour': CronMultiple(
                attrs={'numChoices': 24, 'label': _("hour")}
            ),
            'cron_daymonth': CronMultiple(
                attrs={
                    'numChoices': 31, 'start': 1, 'label': _("day of month"),
                }
            ),
            'cron_dayweek': forms.CheckboxSelectMultiple(
                choices=choices.WEEKDAYS_CHOICES
            ),
            'cron_month': forms.CheckboxSelectMultiple(
                choices=choices.MONTHS_CHOICES
            ),
        }

    def __init__(self, *args, **kwargs):
        super(CronJobForm, self).__init__(*args, **kwargs)
        mchoicefield(self, 'cron_month', [
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
        ])
        mchoicefield(self, 'cron_dayweek', [
            1, 2, 3, 4, 5, 6, 7
        ])

    def clean_cron_user(self):
        user = self.cleaned_data.get("cron_user")
        # See #1061 or FreeBSD PR 162976
        if len(user) > 17:
            raise forms.ValidationError(_(
                "Usernames cannot exceed 17 characters for cronjobs"
            ))
        # Windows users can have spaces in their usernames
        # http://www.freebsd.org/cgi/query-pr.cgi?pr=164808
        if ' ' in user:
            raise forms.ValidationError("Usernames cannot have spaces")
        return user

    def clean_cron_month(self):
        m = self.data.getlist("cron_month")
        if len(m) == 12:
            return '*'
        m = ",".join(m)
        return m

    def clean_cron_dayweek(self):
        w = self.data.getlist('cron_dayweek')
        if w == '*':
            return w
        if len(w) == 7:
            return '*'
        w = ",".join(w)
        return w

    def save(self):
        super(CronJobForm, self).save()
        notifier().restart("cron")


class InitShutdownForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.InitShutdown

    def __init__(self, *args, **kwargs):
        super(InitShutdownForm, self).__init__(*args, **kwargs)
        self.fields['ini_type'].widget.attrs['onChange'] = (
            "initshutdownModeToggle();"
        )

    def clean_ini_command(self):
        _type = self.cleaned_data.get("ini_type")
        val = self.cleaned_data.get("ini_command")
        if _type == 'command' and not val:
            raise forms.ValidationError(_("This field is required"))
        return val

    def clean_ini_script(self):
        _type = self.cleaned_data.get("ini_type")
        val = self.cleaned_data.get("ini_script")
        if _type == 'script' and not val:
            raise forms.ValidationError(_("This field is required"))
        return val


class RsyncForm(ModelForm):

    rsync_create = forms.BooleanField(
        initial = False,
        label = _("Rsync Create"),
        required = False,
        help_text = _("Check this if the remotepath specified does"
		      "<br>not exist, and you want to create it."),
    )

    class Meta:
        
        fields = [
	    'rsync_path',
	    'rsync_user',
	    'rsync_remotehost',
	    'rsync_remoteport',
	    'rsync_mode',
	    'rsync_remotemodule',
	    'rsync_remotepath',
	    'rsync_create',
	    'rsync_direction',
	    'rsync_desc',
	    'rsync_minute',
	    'rsync_hour',
	    'rsync_daymonth',
	    'rsync_month',
	    'rsync_dayweek',
	    'rsync_recursive',
	    'rsync_times',
	    'rsync_compress',
	    'rsync_archive',
	    'rsync_delete',
	    'rsync_quiet',
	    'rsync_preserveperm',
	    'rsync_preserveattr',
	    'rsync_extra',
	    'rsync_enabled'
	] 
        model = models.Rsync
        widgets = {
            'rsync_minute': CronMultiple(
                attrs={'numChoices': 60, 'label': _("minute")}
            ),
            'rsync_hour': CronMultiple(
                attrs={'numChoices': 24, 'label': _("hour")}
            ),
            'rsync_daymonth': CronMultiple(
                attrs={
                    'numChoices': 31, 'start': 1, 'label': _("day of month"),
                }
            ),
            'rsync_dayweek': forms.CheckboxSelectMultiple(
                choices=choices.WEEKDAYS_CHOICES
            ),
            'rsync_month': forms.CheckboxSelectMultiple(
                choices=choices.MONTHS_CHOICES
            ),
        }

    def __init__(self, *args, **kwargs):
        super(RsyncForm, self).__init__(*args, **kwargs)
        mchoicefield(self, 'rsync_month', [
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
        ])
        mchoicefield(self, 'rsync_dayweek', [
            1, 2, 3, 4, 5, 6, 7
        ])
        self.fields['rsync_mode'].widget.attrs['onChange'] = (
            "rsyncModeToggle();"
        )

    def check_rpath_exists(self):
	"""A function to check if the rsync_remotepath,
      	exists or not. Returns TRUE rpath is a directory
      	and exists, else FALSE"""
      	
	ruser = self.cleaned_data.get("rsync_user").encode('utf8')
      	rhost = str(self.cleaned_data.get("rsync_remotehost"))
      	rport = str(self.cleaned_data.get("rsync_remoteport"))
        rpath = self.cleaned_data.get("rsync_remotepath").encode('utf8')
        proc = subprocess.Popen(
                """su -m %s -c "ssh -p %s -o 'BatchMode yes' -o 'ConnectTimeout=5' %s@%s test -d %s" """
                % (ruser,rport,ruser,rhost,rpath), shell=True) 
        proc.wait()
      	return proc.returncode == 0

    def clean_rsync_user(self):
        user = self.cleaned_data.get("rsync_user")
        # See #1061 or FreeBSD PR 162976
        if len(user) > 17:
            raise forms.ValidationError(_(
                "Usernames cannot exceed 17 characters for rsync tasks"
            ))
        # Windows users can have spaces in their usernames
        # http://www.freebsd.org/cgi/query-pr.cgi?pr=164808
        if ' ' in user:
            raise forms.ValidationError(_("Usernames cannot have spaces"))
        return user

    def clean_rsync_remotemodule(self):
        mode = self.cleaned_data.get("rsync_mode")
        val = self.cleaned_data.get("rsync_remotemodule")
        if mode == 'module' and not val:
            raise forms.ValidationError(_("This field is required"))
        return val

    def clean_rsync_remotepath(self):
        mode = self.cleaned_data.get("rsync_mode")
        val = self.cleaned_data.get("rsync_remotepath")
        if mode == 'ssh' and not val:
            raise forms.ValidationError(_("This field is required"))
        return val

    def clean_rsync_month(self):
        m = self.data.getlist("rsync_month")
        if len(m) == 12:
            return '*'
        m = ",".join(m)
        return m

    def clean_rsync_dayweek(self):
        w = self.data.getlist("rsync_dayweek")
        if len(w) == 7:
            return '*'
        w = ",".join(w)
        return w

    def clean_rsync_extra(self):
        extra = self.cleaned_data.get("rsync_extra")
        if extra:
            extra = extra.replace('\n', ' ')
        return extra

    def clean(self):
        cdata = self.cleaned_data
        mode = cdata.get("rsync_mode")
        user = cdata.get("rsync_user")
        if mode == 'ssh':
            try:
                home = pwd.getpwnam(user).pw_dir
                search = os.path.join(home, ".ssh", "id_[edr]*.*")
                if not glob.glob(search):
                    raise ValueError
            except (KeyError, ValueError, AttributeError, TypeError):
                self._errors['rsync_user'] = self.error_class([
                    _("In order to use rsync over SSH you need a user<br />"
                      "with a public key (DSA/ECDSA/RSA) set up in home dir."),
                ])
                cdata.pop('rsync_user', None)
	    if not (self.check_rpath_exists() or self.cleaned_data.get("rsync_create")):
	      self._errors["rsync_remotepath"] = self.error_class(
		[_("The Remote Path you specified does not exist or is not a directory."
		  "<br>Either create one yourself on the remote machine or check the"
		  "<br>'rsync_create' field."
		  "<br>**Note**: This could also happen if the remote path entered"
		  "<br> exceeded 255 characters and was truncated, please restrict it to"
		  "<br>255. Or it could also be that your SSH credentials (remote host,etc) are wrong.")])  
        return cdata

    def save(self):
        super(RsyncForm, self).save()
        notifier().restart("cron")


class SMARTTestForm(ModelForm):

    class Meta:
        fields = '__all__'
        model = models.SMARTTest
        widgets = {
            'smarttest_hour': CronMultiple(
                attrs={'numChoices': 24, 'label': _("hour")}
            ),
            'smarttest_daymonth': CronMultiple(
                attrs={
                    'numChoices': 31, 'start': 1, 'label': _("day of month"),
                }
            ),
            'smarttest_dayweek': forms.CheckboxSelectMultiple(
                choices=choices.WEEKDAYS_CHOICES
            ),
            'smarttest_month': forms.CheckboxSelectMultiple(
                choices=choices.MONTHS_CHOICES
            ),
        }

    def __init__(self, *args, **kwargs):
        if 'instance' in kwargs:
            ins = kwargs.get('instance')
            if ins.smarttest_daymonth == "..":
                ins.smarttest_daymonth = '*/1'
            elif ',' in ins.smarttest_daymonth:
                days = [int(day) for day in ins.smarttest_daymonth.split(',')]
                gap = days[1] - days[0]
                everyx = range(0, 32, gap)[1:]
                if everyx == days:
                    ins.smarttest_daymonth = '*/%d' % gap
            if ins.smarttest_hour == "..":
                ins.smarttest_hour = '*/1'
            elif ',' in ins.smarttest_hour:
                hours = [int(hour) for hour in ins.smarttest_hour.split(',')]
                gap = hours[1] - hours[0]
                everyx = range(0, 24, gap)
                if everyx == hours:
                    ins.smarttest_hour = '*/%d' % gap
        super(SMARTTestForm, self).__init__(*args, **kwargs)
        self.fields.keyOrder.remove('smarttest_disks')
        self.fields.keyOrder.insert(0, 'smarttest_disks')
        mchoicefield(self, 'smarttest_month', [
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
        ])
        mchoicefield(self, 'smarttest_dayweek', [
            1, 2, 3, 4, 5, 6, 7
        ])

    def save(self):
        super(SMARTTestForm, self).save()
        notifier().restart("smartd")

    def clean_smarttest_hour(self):
        h = self.cleaned_data.get("smarttest_hour")
        if h.startswith('*/'):
            each = int(h.split('*/')[1])
            if each == 1:
                return ".."
        return h

    def clean_smarttest_daymonth(self):
        h = self.cleaned_data.get("smarttest_daymonth")
        if h.startswith('*/'):
            each = int(h.split('*/')[1])
            if each == 1:
                return ".."
        return h

    def clean_smarttest_month(self):
        m = eval(self.cleaned_data.get("smarttest_month"))
        m = ",".join(m)
        return m

    def clean_smarttest_dayweek(self):
        w = eval(self.cleaned_data.get("smarttest_dayweek"))
        w = ",".join(w)
        return w

    def clean(self):
        disks = self.cleaned_data.get("smarttest_disks", [])
        test = self.cleaned_data.get("smarttest_type")
        used_disks = []
        for disk in disks:
            qs = models.SMARTTest.objects.filter(
                smarttest_disks__in=[disk],
                smarttest_type=test,
            )
            if self.instance.id:
                qs = qs.exclude(id=self.instance.id)
            if qs.count() > 0:
                used_disks.append(disk.disk_name)
        if used_disks:
            self._errors['smarttest_disks'] = self.error_class([_(
                "The following disks already have tests for this type: %s" % (
                    ', '.join(used_disks),
                )),
            ])
            self.cleaned_data.pop("smarttest_disks", None)
        return self.cleaned_data
