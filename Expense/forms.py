from django.forms import ModelForm
from django import forms

from Expense.models import Purchase, PurchaseItem, Employee

# Create your forms here.

class PurchaseForm(ModelForm):
    class Meta:
        model = Purchase
        fields = []
        

class PurchaseItemForm(ModelForm):
    class Meta:
        model = PurchaseItem
        fields = ['material', 'discount']

class PurchaseFilterForm(forms.Form):
    search = forms.CharField(required=False)
    select_month = forms.CharField(required=False)
    period = forms.CharField(required=False)
    
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        
class EmployeeForm(ModelForm):
    class Meta:
        model = Employee
        fields = ['name', 'daily_rate']