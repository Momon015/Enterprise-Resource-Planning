from django.forms import ModelForm
from django import forms

from Supplier.models import Material, Supplier

from core.models import Category

# Create your forms here.

class MaterialFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
        
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        qs = Category.objects.filter(category_type='item', user=user)
        self.fields['category'].queryset = qs
        
class MaterialForm(ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'price', 'category', 'quantity', 'unit', 'supplier', 'piece_per_unit']
        
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        self.fields['category'].empty_label = None
        self.fields['category'].queryset = Category.objects.filter(category_type='item', user=user)
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        
        self.fields['supplier'].queryset = Supplier.objects.filter(user=user)
        self.fields['supplier'].empty_label = None
        
        self.fields['piece_per_unit'].label = 'Pieces per Unit'
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class SupplierForm(ModelForm):
    class Meta:
        model = Supplier
        fields = ['name']
            

class SupplierFilterForm(forms.Form):
    search = forms.CharField(required=False)   


            
            