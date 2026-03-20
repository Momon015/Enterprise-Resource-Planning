from django.forms import ModelForm
from django import forms

from Supplier.models import Material, Supplier

from core.models import Category

# Create your forms here.

class MaterialFilterForm(forms.Form):
    search = forms.CharField(required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.none(), required=False)
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        qs = Category.objects.filter(category_type='material')
        self.fields['category'].queryset = qs
        
class MaterialForm(ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'price', 'category', 'quantity', 'unit', 'supplier', 'piece_per_unit']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['category'].empty_label = None
        self.fields['supplier'].empty_label = None
        self.fields['category'].queryset = Category.objects.filter(category_type='material')
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        self.fields['piece_per_unit'].label = 'Pieces per Unit'
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class SupplierForm(ModelForm):
    class Meta:
        model = Supplier
        fields = ['name']
            

class SupplierFilterForm(forms.Form):
    search = forms.CharField(required=False)   


            
            