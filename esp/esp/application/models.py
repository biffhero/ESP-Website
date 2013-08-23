from django.db import models
from esp.users.models import ESPUser
from esp.program.models import Program

class FsStudentApp(models.Model):
    """ A student's application. """

    id = models.IntegerField(primary_key=True)
    user = models.ForeignKey(ESPUser)
    program = models.ForeignKey(Program)
    coreclass1 = models.CharField(max_length=80)
    coreclass2 = models.CharField(max_length=80)
    coreclass3 = models.CharField(max_length=80)

    # choices for admin status
    UNREVIEWED = 0
    APPROVED = 1
    ADMITTED = 2
    REJECTED = 3
    WAITLIST = 4
    admin_status = models.IntegerField(default=UNREVIEWED, choices=[
            (UNREVIEWED, 'Unreviewed'),
            (APPROVED, 'Approved'),
            (ADMITTED, 'Admitted'),
            (REJECTED, 'Rejected'),
            (WAITLIST, 'Waitlist')
            ])

    admin_comment = models.TextField()

    def __unicode__(self):
        return "{}'s app for {}".format(self.user, self.program)

    def get_submitted_data(self):
        """ Returns the raw submitted data from the API, as a JSON dict. """

        # return cached copy if available
        if hasattr(self, '_data'):
            return self._data

        fsas = self.program.getModuleExtension('FormstackAppSettings')
        submission = fsas.formstack.submission(self.id)
        self._data = submission
        return submission

    def get_responses(self):
        """ Returns a list of (question, response) tuples from submitted data. """

        data = self.get_submitted_data()
        fsas = self.program.getModuleExtension('FormstackAppSettings')
        info = fsas.get_field_info()
        id_to_label = { field['id']: field['label'] for field in info }
        result = []
        for response in data['data']:
            result.append((id_to_label[response['field']],
                           response['value']))
        return result
