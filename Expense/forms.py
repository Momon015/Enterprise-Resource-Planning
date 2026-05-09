from django.forms import ModelForm
from django import forms

from Expense.models import Purchase, PurchaseItem, Employee, WasteItem, Expense, ExpenseItem, MiscExpense
from Inventory.models import Material

from Supplier.models import Supplier

from core.models import Category
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
    # search = forms.CharField(required=False)
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
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
class EmployeeFilterForm(forms.Form):
    search = forms.CharField(required=False)
        
class MaterialWasteForm(ModelForm):
    class Meta:
        model = WasteItem
        exclude = ['price', 'product', 'waste', 'name', 'supplier']
        
    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            
        self.fields['material'].empty_label = None
        self.fields['material'].label = 'Item'
        self.fields['material'].queryset = Material.objects.filter(stocks__business=business).distinct()
        self.fields['supplier'].queryset = Supplier.objects.filter(business=business)
            
class ProductWasteForm(ModelForm):
    class Meta:
        model = WasteItem
        exclude = ['price', 'material', 'name', 'waste', 'supplier']
    
    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        
        self.fields['supplier'].queryset = Supplier.objects.filter(business=business)
        
        self.fields['product'].empty_label = None
        self.fields['product'].label = 'Finished Product'

class WasteItemFilterForm(forms.Form):
    search = forms.CharField(required=False)
    select_month = forms.CharField(required=False)
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    

class ExpenseForm(ModelForm):
    class Meta:
        model = Expense
        fields = ['total_amount']
        
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        
class MiscExpenseForm(ModelForm):
    category = forms.ModelChoiceField(queryset=Category.objects.none())
    
    class Meta:
        model = MiscExpense
        fields = ['name', 'amount', 'category']
        
    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        self.fields['category'].queryset = Category.objects.filter(category_type='expense', business=business)
        self.fields['category'].empty_label = None
        self.fields['category'].required = False
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        
class ExpenseFilterForm(forms.Form):
    # search = forms.CharField(required=False)
    select_month = forms.CharField(required=False)
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)