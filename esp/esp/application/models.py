from django.db import models, transaction
from django.template import Template, Context
from esp.cache import cache_function
from esp.users.models import ESPUser
from esp.program.models import Program, ClassSubject
from esp.program.modules.base import ProgramModuleObj
from esp.formstack.api import Formstack
from esp.formstack.models import FormstackForm, FormstackSubmission
from esp.lib.markdown import markdown

class FormstackAppSettings(models.Model):
    """
    Settings and helper methods for the Formstack student application
    module.
    """

    module = models.ForeignKey(ProgramModuleObj)

    # formstack settings
    form_id = models.IntegerField(null=True)
    api_key = models.CharField(max_length=80)
    handshake_key = models.CharField(max_length=80, blank=True)
    # end formstack settings

    username_field = models.IntegerField(null=True, blank=True)
    coreclass1_field = models.IntegerField(null=True, blank=True)
    coreclass2_field = models.IntegerField(null=True, blank=True)
    coreclass3_field = models.IntegerField(null=True, blank=True)

    teacher_view_template = models.TextField(blank=True, help_text="""\
A template for what teachers see when they view an app, formatted in
Markdown. To include the content of a field, use {{field.12345}} where
12345 is the field id.""")

    @property
    def formstack(self):
        """
        A reference to the Formstack client using the stored API key.
        """
        return Formstack(self.api_key)

    @property
    def form(self):
        if self.form_id is not None:
            return FormstackForm(self.form_id, self.formstack)
        return None

    def create_username_field(self):
        """
        Creates a form field for ESP Username, returns the field ID,
        and sets the username_field attribute on this object.
        """
        # create a new read-only field
        api_response = self.formstack.create_field(self.form_id, {
                'field_type': 'text',
                'label': 'ESP Username',
                'required': 1,
                'readonly': 1,
                'sort': 1 # puts it at the top of the form
                })
        self.username_field = api_response['id']
        self.save()

class StudentProgramApp(models.Model):
    """ A student's application to the program. """

    user = models.ForeignKey(ESPUser)
    program = models.ForeignKey(Program)

    # choices for admin status
    UNREVIEWED = 0
    APPROVED = 1
    REJECTED = -1
    admin_status = models.IntegerField(default=UNREVIEWED, choices=[
            (UNREVIEWED, 'Unreviewed'),
            (APPROVED, 'Approved'),
            (REJECTED, 'Rejected'),
            ])

    admin_comment = models.TextField(blank=True)

    def __unicode__(self):
        return "{}'s app for {}".format(self.user, self.program)

    def choices(self):
        """
        Returns a dict: preference -> subject, of all the class choices
        tied to this app.
        """
        choices = {}
        for classapp in self.studentclassapp_set.all():
            choices[classapp.student_preference] = classapp.subject
        return choices

    def admitted_to_class(self):
        """
        Returns the ClassSubject this student was accepted to, or None.
        """
        classapps = self.studentclassapp_set.filter(admission_status=StudentClassApp.ADMITTED)
        if classapps.exists():
            assert classapps.count() == 1
            return classapps[0].subject
        else:
            return None

    def waitlisted_to_class(self):
        """
        Returns the ClassSubject(s) this student was waitlisted to.
        """
        result = []
        classapps = self.studentclassapp_set.filter(admission_status=StudentClassApp.WAITLIST)
        for classapp in classapps:
            result.append(classapp.subject)
        return result

class StudentClassApp(models.Model):
    """ A student's application to a particular class. """

    app = models.ForeignKey(StudentProgramApp)
    subject = models.ForeignKey(ClassSubject)
    student_preference = models.PositiveIntegerField()

    teacher_rating = models.PositiveIntegerField(null=True, blank=True)
    teacher_comment = models.TextField(blank=True)

    UNASSIGNED = 0
    ADMITTED = 1
    WAITLIST = 2
    admission_status = models.IntegerField(default=UNASSIGNED, choices=[
            (UNASSIGNED, 'Unassigned'),
            (ADMITTED, 'Admitted'),
            (WAITLIST, 'Waitlist'),
            ])

    def __unicode__(self):
        return "{}'s app for {}".format(self.app.user, self.subject)

    def admit(self):
        # note: this will un-admit the student from all other classes
        for classapp in self.app.studentclassapp_set.all():
            classapp.admission_status = self.UNASSIGNED
            classapp.save()
        self.admission_status = self.ADMITTED
        self.save()

    def unadmit(self):
        self.admission_status = self.UNASSIGNED
        self.save()

    def waitlist(self):
        self.admission_status = self.WAITLIST
        self.save()

    class Meta:
        unique_together = (('app', 'student_preference'),)

class FormstackStudentAppManager(models.Manager):
    locked = False

    @cache_function
    def fetch(self, program):
        """ Get apps for a particular program from the Formstack API. """

        try:
            # avoid infinite recursion
            self.locked = True

            # get submissions from the API
            settings = program.getModuleExtension('FormstackAppSettings')
            submissions = settings.form.submissions()

            # define mapping from string to class subject
            def get_subject(s):
                if s is None: return None
                val, _, _ = s.partition('|')
                try:
                    cls_id = int(val)
                    cls = program.classes().get(id=cls_id)
                    return cls
                except ValueError, ClassSubject.DoesNotExist:
                    cls_title = val.strip()
                    clses = program.classes().filter(anchor__friendly_name=cls_title)
                    if clses:
                        return clses[0]
                    else:
                        return None

            # parse submitted data and make model instances
            apps = []
            for submission in submissions:
                data_dict = { int(entry['field']): entry['value']
                              for entry in submission.data() }

                # link user
                username = data_dict.get(settings.username_field)
                try:
                    user = ESPUser.objects.get(username=username)
                except ESPUser.DoesNotExist:
                    continue # no matching user, ignore

                # link class subjects
                choices = {}
                choices[1] = get_subject(data_dict.get(settings.coreclass1_field))
                choices[2] = get_subject(data_dict.get(settings.coreclass2_field))
                choices[3] = get_subject(data_dict.get(settings.coreclass3_field))

                # update app object, or make one if it doesn't exist
                try:
                    app = self.get(submission_id=submission.id)
                except self.model.DoesNotExist:
                    app = self.model(submission_id=submission.id)
                app.user = user
                app.program = program

                # update class app objects
                for preference, subject in choices.items():
                    if subject is not None:
                        try:
                            classapp = StudentClassApp.objects.get(app=app, student_preference=preference)
                        except StudentClassApp.DoesNotExist:
                            classapp = StudentClassApp(app=app, student_preference=preference)
                        classapp.subject = subject
                apps.append(app)

            # save changes
            with transaction.commit_on_success():
                for app in apps:
                    app.save()
        finally:
            self.locked = False

        return apps
    fetch.depend_on_cache(FormstackForm.submissions, lambda **kwargs: {})
    fetch.depend_on_cache(FormstackSubmission.data, lambda **kwargs: {})

    def get_query_set(self):
        if not self.locked:
            for program in Program.objects.filter(program_modules__handler='FormstackAppModule'):
                self.fetch(program)
        return super(FormstackStudentAppManager, self).get_query_set()

class FormstackStudentApp(StudentProgramApp):
    """ A student's application through Formstack. """

    submission_id = models.IntegerField(unique=True)
    objects = FormstackStudentAppManager()

    @property
    def program_settings(self):
        return self.program.getModuleExtension('FormstackAppSettings')

    @property
    def submission(self):
        return FormstackSubmission(self.submission_id, self.program_settings.formstack)

    def get_responses(self):
        """ Returns a list of (question, response) tuples from submitted data. """

        data = self.submission.data()
        field_info = self.program_settings.field_info()
        id_to_label = { field['id']: field['label'] for field in field_info }
        result = []
        for response in data:
            result.append((id_to_label[response['field']],
                           response['value']))
        return result

    def get_teacher_view(self):
        """ Renders a "teacher view" for an app using a configurable template. """

        data = self.submission.data()
        data_dict = {}
        for response in data:
            data_dict[response['field']] = response['value']
        template = Template(self.program_settings.teacher_view_template)
        context = Context({'fields': data_dict})
        return markdown(template.render(context))

class FormstackStudentClassApp(StudentClassApp):
    """ A student's application to a class through Formstack. """

    def get_responses(self):
        return self.app.formstackstudentapp.responses()

    class Meta:
        proxy = True
