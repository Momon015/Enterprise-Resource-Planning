from django.forms import ModelForm
from django import forms

from Expense.models import Purchase, PurchaseItem, WasteItem, Expense, ExpenseItem, MiscExpense
from Inventory.models import Material

from Supplier.models import Supplier

from core.models import Category
from core.utils.forms import mark_required
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

        mark_required(self)
        
class ExpenseFilterForm(forms.Form):
    # search = forms.CharField(required=False)
    select_month = forms.CharField(required=False)
    start_date = forms.DateField(required=False)
    end_date = forms.DateField(required=False)
    
    
class PurchaseReturnFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={
            'placeholder': 'Reference or refund amount',
            'class': 'form-control form-control--polish',
        }),
    )
    reason = forms.ChoiceField(
        required=False,
        choices=[('', 'All reasons')],  # populated in __init__
        widget=forms.Select(attrs={'class': 'form-control form-control--polish'}),
    )
    select_month = forms.CharField(  # browser <input type="month"> gives "YYYY-MM"
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'month',
            'class': 'form-control form-control--polish',
        }),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control--polish',
        }),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control--polish',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from Expense.models import PurchaseReturn
        self.fields['reason'].choices = [('', 'All reasons')] + list(PurchaseReturn.REASON_CHOICES)

    def clean_q(self):
        # Strip + return None if empty so the view skips filtering cleanly
        q = (self.cleaned_data.get('q') or '').strip()
        return q or None

    def clean(self):
        cleaned = super().clean()
        sd, ed = cleaned.get('start_date'), cleaned.get('end_date')
        if sd and ed and sd > ed:
            raise forms.ValidationError("From date must be before To date.")
