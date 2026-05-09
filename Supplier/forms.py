from django.forms import ModelForm
from django import forms

from Supplier.models import Material, Supplier

from core.models import Category

# Create your forms here.

class MaterialFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
        
    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        qs = Category.objects.filter(category_type='item', business=business)
        self.fields['category'].queryset = qs
        
class MaterialForm(ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'price', 'category', 'quantity', 'unit', 'supplier', 'piece_per_unit']
        
    def __init__(self, *args, **kwargs):
        business = kwargs.pop('business', None)
        super().__init__(*args, **kwargs)
        
        self.fields['category'].empty_label = 'No category'
        self.fields['category'].queryset = Category.objects.filter(category_type='item', business=business)
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        
        self.fields['supplier'].queryset = Supplier.objects.filter(business=business)
        self.fields['supplier'].empty_label = 'No supplier'
        
        self.fields['piece_per_unit'].label = 'Pieces per Unit'
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class SupplierForm(ModelForm):
    class Meta:
        model = Supplier
        fields = ['name']
            

class SupplierFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)   

class PresetFilterForm(forms.Form):
    search = forms.CharField(max_length=100, required=False)
            
            