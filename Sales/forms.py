from django.forms import ModelForm
from django import forms

from Sales.models import Sale, SaleItem

# Create your forms here.

class SaleForm(ModelForm):
    class Meta:
        model = Sale
        fields = ['total_revenue']
        
class SaleFilterForm(forms.Form):
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    select_month = forms.CharField(required=False)
    # search = forms.CharField(required=False)
    