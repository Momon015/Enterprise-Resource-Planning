from django.forms import ModelForm
from django import forms

from Employee.models import Employee

class EmployeeForm(ModelForm):
    class Meta:
        model = Employee
        fields = ['name', 'daily_rate', 'is_cashier']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
class EmployeeFilterForm(forms.Form):
    search = forms.CharField(required=False)