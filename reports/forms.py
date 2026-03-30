from django import forms
from .models import TestCaseRun

class TestCaseStatusForm(forms.ModelForm):
    status = forms.ChoiceField(
        choices=[
            ('passed', 'Passed'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = TestCaseRun
        fields = ['status']