from django.forms import ModelForm
from django import forms

from Supplier.models import Material

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
        fields = ['name', 'price', 'category', 'quantity', 'unit', 'supplier']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.fields['category'].empty_label = None
        self.fields['supplier'].empty_label = None
        self.fields['category'].queryset = Category.objects.filter(category_type='material')
        self.fields['category'].label_from_instance = lambda obj: obj.name.title()
        
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
            

            

            


            
            