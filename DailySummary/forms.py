from django.forms import ModelForm
from django import forms

# Create your forms here.

class SummaryFilterForm(forms.Form):
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    select_month = forms.CharField(required=False)