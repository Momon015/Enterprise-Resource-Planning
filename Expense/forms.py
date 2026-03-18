from django.forms import ModelForm
from django import forms

from Expense.models import Purchase, PurchaseItem, Employee, WasteItem

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
        
class MaterialWasteForm(ModelForm):
    class Meta:
        model = WasteItem
        exclude = ['price', 'user', 'product']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
        self.fields['material'].empty_label = None
        self.fields['material'].label = 'Item'
            
class ProductWasteForm(ModelForm):
    class Meta:
        model = WasteItem
        exclude = ['price', 'user', 'material']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
        
        self.fields['product'].empty_label = None
        self.fields['product'].label = 'Finished Product'

class WasteItemFilterForm(forms.Form):
    search = forms.CharField(required=False)
    select_month = forms.CharField(required=False)
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)