from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Q
import django.forms
import django.forms.widgets
from django.forms.widgets import TextInput

import datetime
from psycopg2.extras import DateTimeTZRange

from selectable.forms.widgets import AutoCompleteSelectWidget, AutoCompleteSelectMultipleWidget

from postgresqleu.util.admin import SelectableWidgetAdminFormMixin
from postgresqleu.util.forms import ConcurrentProtectedModelForm

from postgresqleu.accountinfo.lookups import UserLookup
from postgresqleu.confreg.lookups import RegistrationLookup

from postgresqleu.confreg.models import Conference, ConferenceRegistration, ConferenceAdditionalOption
from postgresqleu.confreg.models import RegistrationClass, RegistrationType, RegistrationDay
from postgresqleu.confreg.models import ConferenceAdditionalOption, ConferenceFeedbackQuestion
from postgresqleu.confreg.models import ConferenceSession, Track, Room
from postgresqleu.confreg.models import ConferenceSessionScheduleSlot, VolunteerSlot
from postgresqleu.confreg.models import DiscountCode

from postgresqleu.confreg.models import valid_status_transitions, get_status_string

class BackendDateInput(TextInput):
	def __init__(self, *args, **kwargs):
		kwargs.update({'attrs': {'type': 'date', 'required-pattern': '[0-9]{4}-[0-9]{2}-[0-9]{2}'}})
		super(BackendDateInput, self).__init__(*args, **kwargs)

class _NewFormDataField(django.forms.Field):
	required=True
	widget=django.forms.HiddenInput

class BackendForm(ConcurrentProtectedModelForm):
	selectize_multiple_fields = None
	vat_fields = {}
	verbose_field_names = {}
	exclude_date_validators = []
	form_before_new = None
	newformdata = None
	_newformdata = _NewFormDataField()
	allow_copy_previous = False
	copy_transform_form = None
	coltypes = {}

	def __init__(self, conference, *args, **kwargs):
		self.conference = conference

		if 'newformdata' in kwargs:
			self.newformdata = kwargs['newformdata']
			del kwargs['newformdata']

		super(BackendForm, self).__init__(*args, **kwargs)

		if self.newformdata:
			self.fields['_newformdata'].initial = self.newformdata
		else:
			del self.fields['_newformdata']


		self.fix_fields()

		for k,v in self.fields.items():
			# Adjust widgets
			if isinstance(v, django.forms.fields.DateField):
				v.widget = BackendDateInput()

			# Any datetime or date fields that are not explicitly excluded will be forced to be within
			# the conference dates.
			if isinstance(v, django.forms.fields.DateTimeField) and not k in self.exclude_date_validators:
				v.validators.extend([
					MinValueValidator(datetime.datetime.combine(self.conference.startdate, datetime.time(0,0,0))),
					MaxValueValidator(datetime.datetime.combine(self.conference.enddate+datetime.timedelta(days=1), datetime.time(0,0,0))),
				])
			elif isinstance(v, django.forms.fields.DateField) and not k in self.exclude_date_validators:
				v.validators.extend([
					MinValueValidator(self.conference.startdate),
					MaxValueValidator(self.conference.enddate),
				])

		for field, vattype in self.vat_fields.items():
			self.fields[field].widget.attrs['class'] = 'backend-vat-field backend-vat-{0}-field'.format(vattype)

	def fix_fields(self):
		pass

	@classmethod
	def get_field_verbose_name(self, f):
		if f in self.verbose_field_names:
			return self.verbose_field_names[f]
		return self.Meta.model._meta.get_field(f).verbose_name.capitalize()

class BackendConferenceForm(BackendForm):
	class Meta:
		model = Conference
		fields = ['active', 'callforpapersopen', 'callforsponsorsopen', 'feedbackopen',
				  'conferencefeedbackopen', 'scheduleactive', 'sessionsactive',
				  'schedulewidth', 'pixelsperminute',
				  'testers', 'talkvoters', 'staff', 'volunteers',
				  'asktshirt', 'askfood', 'askshareemail', 'skill_levels',
				  'additionalintro', 'callforpapersintro', 'sendwelcomemail', 'welcomemail',
				  'invoice_autocancel_hours', 'attendees_before_waitlist']
	selectize_multiple_fields = ['testers', 'talkvoters', 'staff', 'volunteers']


	def fix_fields(self):
		self.fields['testers'].label_from_instance = lambda x: u'{0} {1} ({2})'.format(x.first_name, x.last_name, x.username)
		self.fields['talkvoters'].label_from_instance = lambda x: u'{0} {1} ({2})'.format(x.first_name, x.last_name, x.username)
		self.fields['staff'].label_from_instance = lambda x: u'{0} {1} ({2})'.format(x.first_name, x.last_name, x.username)
		self.fields['volunteers'].label_from_instance = lambda x: u'{0} <{1}>'.format(x.fullname, x.email)
		self.fields['volunteers'].queryset = ConferenceRegistration.objects.filter(conference=self.conference)


class BackendRegistrationForm(BackendForm):
	class Meta:
		model = ConferenceRegistration
		fields = ['firstname', 'lastname', 'company', 'address', 'country', 'phone', 'shirtsize', 'dietary', 'twittername', 'nick', 'shareemail']

	def fix_fields(self):
		if not self.conference.askfood:
			del self.fields['dietary']
		if not self.conference.asktshirt:
			del self.fields['shirtsize']
		if not self.conference.askshareemail:
			del self.fields['shareemail']
		self.update_protected_fields()

class BackendRegistrationClassForm(BackendForm):
	list_fields = ['regclass', 'badgecolor', 'badgeforegroundcolor']
	allow_copy_previous = True
	class Meta:
		model = RegistrationClass
		fields = ['regclass', 'badgecolor', 'badgeforegroundcolor']

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist):
		# Registration classes are copied straight over, but we disallow duplicates
		for id in idlist:
			source = RegistrationClass.objects.get(conference=sourceconf, pk=id)
			if RegistrationClass.objects.filter(conference=targetconf, regclass=source.regclass).exists():
				yield 'A registration class with name {0} already exists.'.format(source.regclass)
			else:
				RegistrationClass(conference=targetconf,
								  regclass=source.regclass,
								  badgecolor=source.badgecolor,
								  badgeforegroundcolor=source.badgeforegroundcolor).save()

class BackendRegistrationTypeForm(BackendForm):
	list_fields = ['regtype', 'regclass', 'cost', 'active', 'sortkey']
	vat_fields = {'cost': 'reg'}
	allow_copy_previous = True
	coltypes = {
		'Sortkey': ['nosearch' ],
	}

	class Meta:
		model = RegistrationType
		fields = ['regtype', 'regclass', 'cost', 'active', 'activeuntil', 'days', 'sortkey', 'specialtype', 'require_phone', 'alertmessage', 'invoice_autocancel_hours', 'requires_option', 'upsell_target']

	def fix_fields(self):
		self.fields['regclass'].queryset = RegistrationClass.objects.filter(conference=self.conference)
		self.fields['requires_option'].queryset = ConferenceAdditionalOption.objects.filter(conference=self.conference)
		if RegistrationDay.objects.filter(conference=self.conference).exists():
			self.fields['days'].queryset = RegistrationDay.objects.filter(conference=self.conference)
		else:
			del self.fields['days']
			self.update_protected_fields()

		if not ConferenceAdditionalOption.objects.filter(conference=self.conference).exists():
			del self.fields['requires_option']
			del self.fields['upsell_target']
			self.update_protected_fields()

	def clean_cost(self):
		if self.instance and self.instance.cost != self.cleaned_data['cost']:
			if self.instance.conferenceregistration_set.filter(Q(payconfirmedat__isnull=False)|Q(invoice__isnull=False)|Q(bulkpayment__isnull=False)).exists():
				raise ValidationError("This registration type has been used, so the cost can no longer be changed")

		return self.cleaned_data['cost']

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist):
		# Registration types are copied straight over, but we disallow duplicates. We also
		# have to match the registration class.
		# NOTE! We do *not* attempt to adjust VAT rates!
		for id in idlist:
			source = RegistrationType.objects.get(conference=sourceconf, pk=id)
			if RegistrationType.objects.filter(conference=targetconf, regtype=source.regtype).exists():
				yield 'A registration type with name {0} already exists.'.format(source.regtype)
			else:
				try:
					if source.regclass:
						targetclass = RegistrationClass.objects.get(conference=targetconf,
																	regclass=source.regclass.regclass)
					else:
						targetclass = None
					RegistrationType(conference=targetconf,
									 regtype=source.regtype,
									 regclass=targetclass,
									 active=source.active,
									 # Not copying activeuntil
									 inlist=source.inlist,
									 sortkey=source.sortkey,
									 specialtype=source.specialtype,
									 # Not copying days
									 alertmessage=source.alertmessage,
									 upsell_target=source.upsell_target,
									 # Not copying invoice_autocancel_hours
									 # Not copying requires_option
					).save()
				except RegistrationClass.DoesNotExist:
					yield 'Could not find registration class {0} for registration type {1}'.format(
						source.regclass.regclass, source.regtype)

class BackendRegistrationDayForm(BackendForm):
	list_fields = [ 'day', ]
	class Meta:
		model = RegistrationDay
		fields = ['day', ]

class BackendAdditionalOptionForm(BackendForm):
	list_fields = ['name', 'cost', 'maxcount', ]
	vat_fields = {'cost': 'reg'}
	class Meta:
		model = ConferenceAdditionalOption
		fields = ['name', 'cost', 'maxcount', 'public', 'upsellable', 'invoice_autocancel_hours',
				  'requires_regtype', 'mutually_exclusive']
	coltypes = {
		'Maxcount': ['nosearch' ],
	}

	def fix_fields(self):
		self.fields['requires_regtype'].queryset = RegistrationType.objects.filter(conference=self.conference)
		self.fields['mutually_exclusive'].queryset = ConferenceAdditionalOption.objects.filter(conference=self.conference).exclude(pk=self.instance.pk)

class BackendTrackForm(BackendForm):
	list_fields = ['trackname', 'sortkey']
	allow_copy_previous = True
	class Meta:
		model = Track
		fields = ['trackname', 'sortkey', 'color', 'incfp']
	coltypes = {
		'Sortkey': ['nosearch' ],
	}

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist):
		# Tracks are copied straight over, but we disallow duplicates
		for id in idlist:
			source = Track.objects.get(conference=sourceconf, pk=id)
			if Track.objects.filter(conference=targetconf, trackname=source.trackname).exists():
				yield 'A track with name {0} already exists.'.format(source.trackname)
			else:
				Track(conference=targetconf,
					  trackname=source.trackname,
					  color=source.color,
					  sortkey=source.sortkey,
					  incfp=source.incfp,
				).save()

class BackendRoomForm(BackendForm):
	list_fields = ['roomname', 'sortkey']
	class Meta:
		model = Room
		fields = ['roomname', 'sortkey']
	coltypes = {
		'Sortkey': ['nosearch' ],
	}

class BackendTransformConferenceDateTimeForm(django.forms.Form):
	timeshift = django.forms.DurationField(required=True, help_text="Shift all times by this much")

	def __init__(self, source, target, *args, **kwargs):
		self.source = source
		self.target = target
		super(BackendTransformConferenceDateTimeForm, self).__init__(*args, **kwargs)
		self.fields['timeshift'].initial = self.source.startdate-self.target.startdate

	def confirm_value(self):
		return str(self.cleaned_data['timeshift'])


class BackendConferenceSessionForm(BackendForm):
	list_fields = [ 'title', 'speaker_list', 'status_string', 'starttime', 'track', 'room']
	verbose_field_names = {
		'speaker_list': 'Speakers',
		'status_string': 'Status',
	}
	selectize_multiple_fields = ['speaker']
	allow_copy_previous = True
	copy_transform_form = BackendTransformConferenceDateTimeForm

	class Meta:
		model = ConferenceSession
		fields = ['title', 'speaker', 'status', 'starttime', 'endtime', 'cross_schedule',
				  'track', 'room', 'can_feedback', 'skill_level', 'abstract', 'submissionnote']

	def fix_fields(self):
		self.fields['track'].queryset = Track.objects.filter(conference=self.conference)
		self.fields['room'].queryset = Room.objects.filter(conference=self.conference)

		if self.instance.status != self.instance.lastnotifiedstatus:
			self.fields['status'].help_text = '<b>Warning!</b> This session has <a href="/events/admin/{0}/sessionnotifyqueue/">pending notifications</a> that have not been sent. You probably want to make sure those are sent before editing the status!'.format(self.conference.urlname)

		if not self.conference.skill_levels:
			del self.fields['skill_level']
			self.update_protected_fields()

	def clean(self):
		cleaned_data = super(BackendConferenceSessionForm, self).clean()

		if cleaned_data.get('starttime') and not cleaned_data.get('endtime'):
			self.add_error('endtime', 'End time must be specified if start time is!')
		elif cleaned_data.get('endtime') and not cleaned_data.get('starttime'):
			self.add_error('starttime', 'Start time must be specified if end time is!')
		elif cleaned_data.get('starttime') and cleaned_data.get('endtime'):
			if cleaned_data.get('endtime') < cleaned_data.get('starttime'):
				self.add_error('endtime', 'End time must be later than start time!')

		if cleaned_data.get('cross_schedule') and cleaned_data.get('room'):
			self.add_error('room', 'Room cannot be specified for cross schedule sessions!')

		return cleaned_data

	def clean_status(self):
		newstatus = self.cleaned_data.get('status')
		if newstatus == self.instance.status:
			return newstatus

		# If there are speakers on the session, we lock it to the workflow. For sessions
		# with no speakers, anything goes
		if not self.cleaned_data.get('speaker').exists():
			return newstatus

		if not newstatus in valid_status_transitions[self.instance.status]:
			raise ValidationError("Sessions with speaker cannot change from {0} to {1}. Only one of {2} is allowed.".format(
				get_status_string(self.instance.status),
				get_status_string(newstatus),
				", ".join(["{0} ({1})".format(get_status_string(s), v) for s,v in valid_status_transitions[self.instance.status].items()]),
			))

		return newstatus

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		for id in idlist:
			source = ConferenceSession.objects.get(conference=sourceconf, pk=id)
			try:
				if source.track:
					targettrack = Track.objects.get(conference=targetconf,
													trackname=source.track.trackname)
				else:
					targettrack = None
				s = ConferenceSession(conference=targetconf,
									  title=source.title,
									  starttime=source.starttime and source.starttime + xform,
									  endtime=source.starttime and source.endtime + xform,
									  track=targettrack,
									  cross_schedule=source.cross_schedule,
									  can_feedback=source.can_feedback,
									  abstract=source.abstract,
									  skill_level=source.skill_level,
									  status=0,
									  submissionnote=source.submissionnote,
									  initialsubmit=source.initialsubmit,
				)
				s.save()
				for spk in source.speaker.all():
					s.speaker.add(spk)

			except Track.DoesNotExist:
				yield 'Could not find track {0}'.format(source.track.trackname)

	@classmethod
	def get_transform_example(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		slotlist = ConferenceSession.objects.filter(conference=sourceconf, id__in=idlist, starttime__isnull=False)[:2]
		if slotlist:
			return " and ".join(["time {0} becomes {1}".format(s.starttime, s.starttime + xform) for s in slotlist])

		# Do we have sessions without time?
		slotlist = ConferenceSession.objects.filter(conference=sourceconf, id__in=idlist)
		if slotlist:
			return "no scheduled sessions picked, so no transformation will happen"
		return None

class BackendConferenceSessionSlotForm(BackendForm):
	list_fields = [ 'starttime', 'endtime', ]
	allow_copy_previous = True
	copy_transform_form = BackendTransformConferenceDateTimeForm

	class Meta:
		model = ConferenceSessionScheduleSlot
		fields = ['starttime', 'endtime' ]

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		for id in idlist:
			source = ConferenceSessionScheduleSlot.objects.get(conference=sourceconf, pk=id)
			ConferenceSessionScheduleSlot(conference=targetconf,
										  starttime=source.starttime + xform,
										  endtime=source.endtime + xform,
										  ).save()
		return
		yield None # Turn this into a generator

	@classmethod
	def get_transform_example(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		slotlist = [ConferenceSessionScheduleSlot.objects.get(conference=sourceconf, id=i) for i in idlist[:2]]
		xstr = " and ".join(["time {0} becomes {1}".format(s.starttime, s.starttime + xform) for s in slotlist])
		return xstr


class BackendVolunteerSlotForm(BackendForm):
	list_fields = [ 'timerange', 'title', 'min_staff', 'max_staff' ]
	allow_copy_previous = True
	copy_transform_form = BackendTransformConferenceDateTimeForm

	class Meta:
		model = VolunteerSlot
		fields = [ 'timerange', 'title', 'min_staff', 'max_staff' ]
	coltypes = {
		'Min staff': ['nosearch' ],
		'Max staff': ['nosearch' ],
	}

	def clean(self):
		cleaned_data = super(BackendVolunteerSlotForm, self).clean()
		if cleaned_data.get('min_staff') > cleaned_data.get('max_staff'):
			self.add_error('max_staff', 'Max staff must be at least as high as min_staff!')

		return cleaned_data

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		for id in idlist:
			source = VolunteerSlot.objects.get(conference=sourceconf, pk=id)
			VolunteerSlot(conference=targetconf,
						  timerange=DateTimeTZRange(source.timerange.lower + xform,
													source.timerange.upper + xform),
						  title=source.title,
						  min_staff=source.min_staff,
						  max_staff=source.max_staff,
			).save()
		return
		yield None # Turn this into a generator

	@classmethod
	def get_transform_example(self, targetconf, sourceconf, idlist, transformform):
		xform = transformform.cleaned_data['timeshift']
		if not idlist:
			return None
		s = VolunteerSlot.objects.get(conference=sourceconf, id=idlist[0])
		return "range {0}-{1} becomes {2}-{3}".format(
			s.timerange.lower, s.timerange.upper,
			s.timerange.lower + xform, s.timerange.upper + xform,
		)

class BackendFeedbackQuestionForm(BackendForm):
	list_fields = ['newfieldset', 'question', 'sortkey',]
	allow_copy_previous = True

	class Meta:
		model = ConferenceFeedbackQuestion
		fields = ['question', 'isfreetext', 'textchoices', 'sortkey', 'newfieldset']
	coltypes = {
		'Sortkey': ['nosearch' ],
	}

	@classmethod
	def copy_from_conference(self, targetconf, sourceconf, idlist):
		# Conference feedback questions are copied straight over, but we disallow duplicates
		for id in idlist:
			source = ConferenceFeedbackQuestion.objects.get(conference=sourceconf, pk=id)
			if ConferenceFeedbackQuestion.objects.filter(conference=targetconf, question=source.question).exists():
				yield 'A question {0} already exists.'.format(source.question)
			else:
				ConferenceFeedbackQuestion(conference=targetconf,
										   question=source.question,
										   isfreetext=source.isfreetext,
										   textchoices=source.textchoices,
										   sortkey=source.sortkey,
										   newfieldset=source.newfieldset,
										   ).save()


class BackendNewDiscountCodeForm(django.forms.Form):
	codetype = django.forms.ChoiceField(choices=((1, 'Fixed amount discount'), (2, 'Percentage discount')))

	def get_newform_data(self):
		return self.cleaned_data['codetype']

class BackendDiscountCodeForm(BackendForm):
	list_fields = ['code', 'validuntil', 'maxuses']

	form_before_new = BackendNewDiscountCodeForm

	exclude_date_validators = ['validuntil', ]

	class Meta:
		model = DiscountCode
		fields = ['code', 'discountamount', 'discountpercentage', 'regonly', 'validuntil', 'maxuses',
				  'requiresregtype', 'requiresoption']

	def fix_fields(self):
		if self.newformdata == "1" and not self.instance.discountamount:
			self.instance.discountamount = 1
		elif self.newformdata == "2" and not self.instance.discountpercentage:
			self.instance.discountpercentage = 1

		if self.instance.discountamount:
			# Fixed amount discount
			del self.fields['discountpercentage']
			del self.fields['regonly']
			self.fields['discountamount'].validators.append(MinValueValidator(1))
		else:
			# Percentage discount
			del self.fields['discountamount']
			self.fields['discountpercentage'].validators.extend([
				MinValueValidator(1),
				MaxValueValidator(99),
			])
		self.fields['maxuses'].validators.append(MinValueValidator(0))

		self.fields['requiresregtype'].queryset = RegistrationType.objects.filter(conference=self.conference)
		self.fields['requiresoption'].queryset = ConferenceAdditionalOption.objects.filter(conference=self.conference).exclude(pk=self.instance.pk)

		self.update_protected_fields()


#
# Form to pick a conference to copy from
#
class BackendCopySelectConferenceForm(django.forms.Form):
	conference = django.forms.ModelChoiceField(Conference.objects.all())

	def __init__(self, request, conference, model, *args, **kwargs):
		super(BackendCopySelectConferenceForm, self).__init__(*args, **kwargs)
		self.fields['conference'].queryset = Conference.objects.filter(administrators=request.user).exclude(pk=conference.pk).extra(
			where=["EXISTS (SELECT 1 FROM {0} WHERE conference_id=confreg_conference.id)".format(model._meta.db_table), ]
		)