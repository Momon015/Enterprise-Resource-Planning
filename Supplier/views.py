from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404, HttpResponseNotAllowed
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.views.decorators.http import require_POST
from django.urls import reverse

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from Supplier.models import Material, MaterialPreset, MaterialPresetItem, Supplier
from Supplier.forms import MaterialForm, MaterialFilterForm, SupplierForm, SupplierFilterForm, PresetFilterForm

from django.core.paginator import Paginator

from django.db.models import Q, F, Sum, Max, Avg, Count, DecimalField, Value
from django.db.models.functions import Coalesce
from decimal import Decimal

from user.models import User

from core.utils.owner import permission_required, get_queryset_for_user, get_business_for_user
from core.utils.cart import prune_stale_cart_lines
from core.utils.htmx import redirect_after_form, back_url
from core.utils.forms import add_duplicate_name_error

from django.contrib.messages import get_messages

from django.core.exceptions import ValidationError

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user



# Create your views here

@login_required(login_url='login')
def material_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    cart = prune_stale_cart_lines(request, business, 'cart', Material)
    total = 0
    
    cart_items = []
    
    if cart:
        for material_id, data in cart.items():
            material = get_object_or_404(Material, business=business, id=material_id)
            
            # computations
            line_total = data.get('quantity', 0) * material.price
            total += line_total
            
            cart_items.append({
                'id': material.id,
                'name': material.name,
                'quantity': data.get('quantity', 0),
                'price': material.price,
                'line_total': line_total,
                'unit': material.unit,
            })
            
    form = MaterialFilterForm(request.GET or None, business=business)
    
    materials = get_queryset_for_user(request.user, Material.objects.all()).filter(business=business).order_by('is_locked', 'name')

    
    """
    this allows to filter things without 
    causing any bugs like not showing anything 
    in template to ensure this always work
    """
    categories = form.fields['category'].queryset
    categories_count = categories.count()
    
    # top 3 categories
    top_categories = categories.annotate(
        material_count=Count('materials')
    ).exclude(name='No category') \
    .order_by('-material_count')[:3]
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = request.GET.get('category')
        
        unit_map = {label.lower(): key for key, label in Material.RETAIL_UNIT_CHOICES}
        matched_unit = unit_map.get(search.lower())
        
        if search:
            materials = materials.filter(
                Q(name__icontains=search) |
                    Q(price__icontains=search) |
                    Q(quantity__icontains=search) |
                    Q(unit__icontains=matched_unit or search)
                    # Q(category__name__icontains=search) |
                    
            )   
        if category:
            materials = materials.filter(category=category)
        
    # pagination
    paginator = Paginator(materials, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
           

    suppliers = get_queryset_for_user(request.user, Supplier.objects.all()).filter(business=business).order_by('-name')
    
    recent_events = ActivityEvent.objects.filter(
        Q(verb__startswith='material.') |
        Q(verb__startswith='purchase.') |
        Q(verb__startswith='stock.'),
        business=business
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    archived_count = Material.all_objects.filter(business=business, status='inactive').count()
    
    context = {
        'categories': categories, 
        'page_obj': page_obj, 
        'total': total,
        'suppliers': suppliers,
        'categories_count': categories_count,
        'top_categories': top_categories,
        'section': 'material',
        'recent_events': recent_events,
        'archived_count': archived_count,

        # HTMX
        'cart_count': sum(item['quantity'] for item in cart.values()),
        'clear_sessions': 'clear-cart',
        'cart_items': len(cart),
        'name': 'Materials',
        'total_name': 'cost',
        'type': 'purchase',
    
        }
    
    return render(request, 'Supplier/material_list.html', context)

@login_required(login_url='login')
@capacity_required('material')
@permission_required('create') # dev
def material_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    if request.method == 'POST':
        form = MaterialForm(request.POST, business=business)
        
        if form.is_valid():
            material = form.save(commit=False)
            existing = Material.all_objects.filter(
                business=business,
                name__iexact=material.name.title(),
                unit=material.unit,
            ).first()
            
            if existing:
                # Inline on the field — see add_duplicate_name_error. Material says
                # archived with `status`, not `is_active` like Product does.
                add_duplicate_name_error(form, existing, archived=existing.status == 'inactive')
            else:
                material.user = business.user
                material.created_by = request.user
                material.name = material.name.title()
                material.business = business
                material.save()

                log_activity(business, request.user, 'material.created',
                    target=material, description=f"{material.name} added")

                messages.success(request, f"{material.name} successfully created.")
                return redirect_after_form(request, 'material-list', business_slug=business.slug)
    else:
        form = MaterialForm(business=business)

    context = {'form': form, 'section': 'material'}

    # htmx → render just the modal partial (opened from the material list)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_material_form_modal.html', context)

    return render(request, 'Supplier/material_create.html', context)

@login_required(login_url='login')
def material_detail(request, slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    material = get_object_or_404(Material, business=business, slug=slug, id=id)
    
    context = {'material': material, 'section': 'material'}
    return render(request, 'Supplier/material_detail.html', context)

@login_required(login_url='login')
@permission_required('update')
def material_update(request,  slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
        
    material = get_object_or_404(Material, business=business, slug=slug, id=id)

    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material, business=business)
        
        if form.is_valid():
            material = form.save(commit=False)
            material.name = material.name.title()
            # material.user = business.user
            material.save()
            
            log_activity(business, request.user, 'material.updated',
                target=material, description=f"{material.name} updated")

            
            messages.success(request, f"{material.name} successfully updated.")
            return redirect_after_form(request, 'material-list', business_slug=business.slug)

    else:
        form = MaterialForm(instance=material, business=business)

    context = {'form': form, 'material': material, 'section': 'material'}

    # htmx → render just the modal partial (opened from the material list / detail)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_material_update_modal.html', context)

    return render(request, 'Supplier/material_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('delete') # dev
def material_archive(request, slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    material = get_object_or_404(Material, business=business, slug=slug, id=id)

    if request.method == 'POST':
        material.status = 'inactive'
        material.save(update_fields=['status'])
        log_activity(business, request.user, 'material.archived',
            target=material, description=f"{material.name} archived")
        messages.success(request, f"{material.name} archived. Linked product was archived too.")
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('material-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('material-list', business_slug=business.slug)

    if request.headers.get('HX-Request'):
        cat = getattr(material.category, 'name', 'No category')
        sup = getattr(material.supplier, 'name', 'No supplier')
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': f"{material.name}",
            'cm_subtitle': f"{cat} · {sup}",
            'cm_note': "Hidden from your materials list · Its linked product is archived too · <strong>Restore anytime</strong>.",
            'cm_action': reverse('material-archive', kwargs={
                'business_slug': business.slug, 'slug': material.slug, 'id': material.id}),
            'cm_label': "Confirm Archive",
            'cm_icon': 'bi-box-seam',
        })

    context = {'material': material, 'section': 'material'}
    return render(request, 'Supplier/material_archive.html', context)


@login_required(login_url='login')
@permission_required('owner_only')  # owner
@permission_required('add') # dev
def archived_materials(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    material = Material.all_objects.filter(business=business, status='inactive').order_by('-id')
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/partials/_archived_materials_modal.html', {
            'materials': material,
            'business': business,
        })
    return render(request, 'Supplier/archived_materials.html', {
        'materials': material,
        'business': business,
        'section': 'material'
    })

@login_required(login_url='login')
@permission_required('owner_only') # owner
@permission_required('add') # dev 
def restore_material(request, business_slug, material_id):
    business = get_business_for_user(request.user, business_slug)
    material = get_object_or_404(Material.all_objects, business=business, id=material_id, status='inactive')
    if request.method == 'POST':
        material.status = 'active'
        material.save(update_fields=['status'])
        
        log_activity(business, request.user, 'material.restored',
            target=material, description=f"{material.name} restored")

        messages.success(request, f"{material.name} restored. Linked product was also restored.")
        if request.headers.get('HX-Request'):
            materials = Material.all_objects.filter(business=business, status='inactive').order_by('-id')
            return render(request, 'Supplier/partials/_archived_materials_modal.html', {
                'materials': materials,
                'business': business,
                'reload_on_close': True,
            })
    return redirect('archived-materials', business_slug=business.slug)

@login_required(login_url='login')
@capacity_required('material_preset')
@permission_required('add') # dev
def save_items(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = prune_stale_cart_lines(request, business, 'cart', Material)
    
    if request.method == 'POST':
        checkbox = request.POST.get('checkbox')
        # Strip before testing: a name of "   " is truthy, and used to save a preset
        # with a blank-looking title. Title-case here so the name we check for a clash
        # is the same one we store and echo back.
        name = (request.POST.get('name') or '').strip().title()

        if not checkbox and not name:
            messages.warning(request, "Please add a preset title and don't forget to check the checkbox.")

        elif not name:
            messages.warning(request, "You forgot to add a preset title.")

        elif not checkbox:
            messages.warning(request, "You forgot to click the checkbox.")

        elif not cart:
            messages.warning(request, "Your cart is empty. Add materials first, then save them as a preset.")

        elif MaterialPreset.objects.filter(business=business, name=name).exists():
            # The name is unique per business (MaterialPreset.Meta.unique_together), so
            # get_or_create handed back the EXISTING preset and then get_or_create'd each
            # item — leaving the old quantities untouched while reporting success. Say the
            # name is taken rather than silently doing nothing or silently overwriting.
            messages.warning(request, f"You already have a preset called {name}. Pick a different name.")

        else:
            preset = MaterialPreset.objects.create(
                user=business.user,
                business=business,
                name=name,
                is_active=True,
                created_by=request.user,
            )

            for material_id, data in cart.items():

                material = get_object_or_404(Material, business=business, id=material_id)

                MaterialPresetItem.objects.create(
                    preset=preset,
                    material=material,
                    quantity=data['quantity'],
                    discount=data.get('discount', 0),
                )
            messages.success(request, f"{name} added to preset.")
            request.session['preset_id'] = preset.id
        return redirect('view-cart', business_slug=business.slug)

@login_required(login_url='login')
@permission_required('add') # dev
def adding_preset_to_cart(request, preset_id, business_slug):
    business = get_business_for_user(request.user, business_slug)

    cart = request.session.get('cart', {})
    
    preset = get_object_or_404(MaterialPreset, business=business, id=preset_id)
    items = preset.preset_items.select_related('material')
    
    added_count = 0
    failed_count = 0
    locked_count = 0

    # GUARD 1 — the preset is over the plan's preset cap. This lock is about the PRESET
    # itself, so refuse the whole thing. The list has always shown a "Locked" badge that
    # nothing actually enforced. Locks on the materials INSIDE are guard 2, handled per
    # item: a material-level fact must not become a preset-level consequence.
    # (Replaces `if preset:`, which get_object_or_404 above already guarantees.)
    if preset.is_locked:
        messages.warning(request, f"{preset.name} is locked - upgrade your plan or unlock it to use.")
    else:
        for item in items:
            material = item.material

            # GUARD 2 — a locked material is over the plan's material cap, so a preset
            # must not quietly pull it back into a purchase. Counted apart from
            # failed_count so the summary doesn't blame the "quantity limit" for what is
            # really a plan state the owner has to act on.
            if material.is_locked:
                locked_count += 1
                continue

            material_key = str(material.id)

            """
            The cart.get() will get the material_key if it exists
            then it will get the quantity else 0 and it will not
            throw a KeyError if there is a low stock in inventory
            because in your condition you added both item's quantity
            and cart's quantity to check if it's low then It will not
            be added to the cart session.
            """
            existing_qty = cart.get(material_key, {}).get('quantity', 0)

            if material_key in cart:
                if material.quantity >= item.quantity + existing_qty:
                    cart[material_key]['quantity'] += item.quantity
                    added_count += 1
                else:
                    failed_count += 1
            else:
                cart[material_key] = {
                    'id': item.material.id,
                    'name': item.material.name,
                    'quantity': item.quantity,
                    'price': f"{item.material.price:.2f}",
                    'discount': f"{item.discount:.2f}",
                }
                added_count += 1
                
        # Show ONE summary message
        if added_count > 0:
            messages.success(request, f"{preset.name} - {added_count} item(s) added to purchase.")
        if failed_count > 0:
            messages.warning(request, f"{failed_count} item(s) couldn't be added (quantity limit).")
        if locked_count > 0:
            messages.warning(request, f"{locked_count} item(s) are locked - upgrade your plan or unlock them to buy.")


    request.session['cart'] = cart
    request.session.modified = True
    
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_preset_response.html', {
            'cart_url': 'view-cart',
            'icon': 'bi-cart3',
            'badge_id': 'purchase-cart-badge',
            'badge_mod': 'topbar-cart--purchase',
            'cart_title': 'Purchase cart',
            'label': 'Purchase Record',
            'cart_count': sum(item['quantity'] for item in cart.values()),
            'cart_items': len(cart),
            'messages': get_messages(request),
            'section': 'material',
        })

    # ?next= so we stay on the detail page the user added from
    next_url = request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    # otherwise stay on the preset list, preserving filters
    query_string = request.META.get('QUERY_STRING', '')
    url = reverse('material-preset-list', kwargs={'business_slug': business.slug})
    return redirect(f"{url}?{query_string}" if query_string else url)

    
@login_required(login_url='login')
def preset_list(request, business_slug):
    cart = request.session.get('cart', {})
    
    business = get_business_for_user(request.user, business_slug)
    
    presets = get_queryset_for_user(request.user, MaterialPreset.objects.all()).filter(business=business).order_by('is_locked', 'name')
    
    form = PresetFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            presets = presets.filter(name__icontains=search)
    
    paginator = Paginator(presets, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj, 
        'section': 'material',
        
        # HTMX
        'cart_url': 'view-cart',
        'label': 'Purchase Record',
        'cart_count': sum(item['quantity'] for item in cart.values()),
        'cart_items': len(cart),
        'messages': get_messages(request)
    }
    return render(request, 'Supplier/list_preset.html', context)

@login_required(login_url='login')
def preset_detail(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
    
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)

    # Add to Cart sends the user back HERE — which is the list when this is a modal.
    context = {'preset': preset, 'back_url': back_url(request), 'section': 'material'}

    # htmx → render just the modal partial (opened from the preset list)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_preset_detail_modal.html', context)

    return render(request, 'Supplier/detail_preset.html', context)

@login_required(login_url='login')
@permission_required('update') # dev
def edit_preset(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
        
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)
    save_items = preset.preset_items.select_related('material')
    
    qty_changed = False
    discount_changed = False
    
    if request.method == 'POST':
        for item in save_items:
            raw_qty = request.POST.get(f'quantity_{item.id}')
            new_qty = int(raw_qty)
            
            raw_discount = request.POST.get(f"discount_{item.id}")
            new_discount = int(raw_discount)
            
            new_name = request.POST.get(f'preset_{preset.id}')
            
            if new_name and new_name != preset.name: # validate the name with max and min length 
                preset.name = new_name.title()
                preset.save()
                messages.success(request, f"Preset Name has been updated. ")
                    
            if new_qty and new_qty != item.quantity:
                item.quantity = new_qty
                item.save()
                qty_changed = True
                
            if new_discount and new_discount != item.discount: 
                item.discount = new_discount
                item.save()
                discount_changed = True
                
        if qty_changed == True and discount_changed == True:
            messages.success(request, f"Both has been updated. ")

        if qty_changed == True and not discount_changed == True:
            messages.success(request, f"{item.material.name}'s quantity has been updated. ")
                    
        if discount_changed == True and not qty_changed == True:
            messages.success(request, f"{item.material.name}'s discount has been updated. ")
        
        return redirect_after_form(request, 'material-preset-list', business_slug=business.slug)

    context = {'preset': preset, 'items': save_items, 'section': 'material'}

    # htmx → render just the modal partial (opened from the preset list / detail modal)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_preset_edit_modal.html', context)

    return render(request, 'Supplier/edit_preset.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def delete_preset(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
    
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)
    
    if request.method == 'POST':
        name = preset.name          # read it BEFORE the row is gone
        preset.delete()
        messages.success(request, f"{name} has been deleted.")
        return redirect_after_form(request, 'material-preset-list', business_slug=business.slug)

    # htmx → the shared confirm modal, same component archive/void/payment use.
    # The full-page delete_preset.html stays as the no-JS fallback.
    if request.headers.get('HX-Request'):
        count = preset.preset_items.count()
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': preset.name,
            'cm_subtitle': f"{count} material{'' if count == 1 else 's'} · saved {preset.created_at:%b %d, %Y}",
            'cm_note': "Deletes the preset only — your <strong>materials and purchase history "
                       "are not affected</strong>. This can’t be undone.",
            'cm_tone': 'danger',
            'cm_icon': 'bi-bookmark-fill',
            'cm_action': reverse('material-delete-preset', kwargs={
                'business_slug': business.slug, 'id': preset.id, 'slug': preset.slug}),
            'cm_label': "Confirm Delete",
            'cm_btn_icon': 'bi-trash3-fill',
        })

    context = {'preset': preset, 'section': 'material'}
    return render(request, 'Supplier/delete_preset.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def remove_preset_item(request, business_slug, id, item_id):
    business = get_business_for_user(request.user, business_slug)
    preset = get_object_or_404(MaterialPreset, business=business, id=id)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Empty-preset guard
    if preset.preset_items.count() <= 1:
        messages.error(request, "A preset needs at least one item. Delete the preset instead.")
        return HttpResponse(status=204, headers={'HX-Refresh': 'true'})

    item = get_object_or_404(MaterialPresetItem, id=item_id, preset=preset)
    name = item.material.name
    item.delete()
    messages.success(request, f"{name} removed from {preset.name}.")
    return HttpResponse("")  # htmx swaps the row out with empty content

def supplier_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    suppliers = get_queryset_for_user(request.user, Supplier.objects.all()).filter(business=business).order_by('is_locked', 'name')
    form = SupplierFilterForm(request.GET or None)

    # Always annotate MTD spend + last order (independent of the search filter)
    month_start = timezone.localdate().replace(day=1)
    suppliers = suppliers.annotate(
        last_order=Max('materials__items__purchase__purchase_date'),
        spend_mtd=Coalesce(
            Sum(
                (F('materials__items__price') * F('materials__items__quantity'))
                    - F('materials__items__discount'),
                filter=Q(materials__items__purchase__purchase_date__gte=month_start),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    if form.is_valid():
        search = form.cleaned_data.get('search')
        if search:
            suppliers = suppliers.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(contact_number__icontains=search)
            )

        
    pagination = Paginator(suppliers, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    recent_events = ActivityEvent.objects.filter(
        Q(verb__startswith='supplier.') |
        Q(verb__startswith='purchase.'),
        business=business
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    from core.utils.kpis import get_supplier_kpis
    kpis = get_supplier_kpis(business)

    context = {
        'page_obj': page_obj, 
        'section': 'supplier',
        'recent_events': recent_events,
        'kpis': kpis,
        }
    return render(request, 'Supplier/supplier_list.html', context)

@login_required(login_url='login')
@capacity_required('supplier')
@permission_required('add') # dev
def supplier_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    if request.method == 'POST':
        form = SupplierForm(request.POST, request.FILES)
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.user = business.user
            supplier.created_by = request.user
            supplier.business = business
            supplier.name = supplier.name.title()
            try:
                supplier.save()
                log_activity(business, request.user, 'supplier.created',
                    target=supplier, description=f"{supplier.name} added")

            except ValidationError as e:
                messages.warning(request, e.messages[0])
                return redirect_after_form(request, 'supplier-list', business_slug=business.slug)

            messages.success(request, f"{supplier.name} successfully created.")
            return redirect_after_form(request, 'supplier-list', business_slug=business.slug)
    else:
        form = SupplierForm()

    context = {'form': form, 'section': 'supplier'}

    # htmx → render just the modal partial (opened from the supplier list)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_supplier_form_modal.html', context)

    return render(request, 'Supplier/supplier_create.html', context)

# @login_required(login_url='login')
# def supplier_detail(request, supplier_id):
#     supplier = get_object_or_404(Supplier, id=supplier_id)
    
#     context = {'supplier': supplier, 'section': 'Supplier'}
#     return render(request, 'Supplier/supplier_detail.html', context)

@login_required(login_url='login')
@permission_required('update') # dev
def supplier_update(request, business_slug, supplier_id, slug):
    business = get_business_for_user(request.user, business_slug)
    supplier = get_object_or_404(Supplier, business=business, id=supplier_id, slug=slug)

    # A GET exit, so it needs the htmx-aware redirect too: an hx-get answered with a
    # plain 302 has the LIST swapped into the modal instead of navigating.
    if supplier.slug == 'no-supplier':
        messages.warning(request, '"No Supplier" is a system default and cannot be edited — it holds materials that have no supplier assigned.')
        return redirect_after_form(request, 'supplier-list', business_slug=business.slug)

    if request.method == 'POST':
        form = SupplierForm(request.POST, request.FILES, instance=supplier)

        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.name = supplier.name.title()
            supplier.save()

            log_activity(business, request.user, 'supplier.updated',
                target=supplier, description=f"{supplier.name} updated")

            messages.success(request, f"{supplier.name} successfully updated.")
            return redirect_after_form(request, 'supplier-list',
                                       query=request.META.get('QUERY_STRING', ''),
                                       business_slug=business.slug)
    else:
        form = SupplierForm(instance=supplier)

    context = {'form': form, 'supplier': supplier, 'section': 'supplier'}

    # htmx → render just the modal partial (opened from the supplier list)
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/_supplier_update_modal.html', context)

    return render(request, 'Supplier/supplier_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('delete') # dev
def supplier_archive(request, business_slug, supplier_id, slug):
    business = get_business_for_user(request.user, business_slug)
    supplier = get_object_or_404(Supplier, business=business, id=supplier_id, slug=slug)
    is_hx = request.headers.get('HX-Request')

    def back_to_list():
        if is_hx:
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('supplier-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('supplier-list', business_slug=business.slug)

    if supplier.slug == 'no-supplier':
        messages.warning(request, '"No Supplier" is a system default and cannot be archived — it holds materials that have no supplier assigned.')
        return back_to_list()

    if request.method == 'POST':
        supplier.status = 'inactive'
        try:
            supplier.full_clean()                # your clean() stock check
        except ValidationError as e:
            messages.warning(request, e.messages[0])
            return back_to_list()
        supplier.save(update_fields=['status'])
        log_activity(business, request.user, 'supplier.archived',
             target=supplier, description=f"{supplier.name} archived")
        messages.success(request, f"{supplier.name} archived (status: inactive).")
        return back_to_list()

    if is_hx:
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': f"{supplier.name}",
            'cm_note': "Hidden from your supplier list · Past purchases kept · <strong>Restore anytime</strong>.",
            'cm_action': reverse('supplier-archive', kwargs={
                'business_slug': business.slug, 'supplier_id': supplier.id, 'slug': supplier.slug}),
            'cm_label': "Confirm Archive",
            'cm_icon': 'bi-truck',
        })

    return render(request, 'Supplier/supplier_archive.html', {'supplier': supplier})





@login_required(login_url='login')
@permission_required('owner_only')  # owner
@permission_required('add') # dev
def archived_suppliers(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    supplier = Supplier.all_objects.filter(business=business, status='inactive').order_by('-id')
    if request.headers.get('HX-Request'):
        return render(request, 'Supplier/partials/_archived_suppliers_modal.html', {
            'suppliers': supplier,
            'business': business,
        })
    return render(request, 'Supplier/archived_suppliers.html', {
        'suppliers': supplier,
        'business': business,
        'section': 'supplier'
    })

@login_required(login_url='login')
@permission_required('owner_only') # owner
@permission_required('add') # dev 
def restore_supplier(request, business_slug, supplier_id):
    business = get_business_for_user(request.user, business_slug)
    supplier = get_object_or_404(Supplier.all_objects, business=business, id=supplier_id, status='inactive')
    if request.method == 'POST':
        supplier.status = 'active'
        supplier.save(update_fields=['status'])
        
        log_activity(business, request.user, 'supplier.restored',
             target=supplier, description=f"{supplier.name} restored")

        messages.success(request, f"{supplier.name} restored. Linked product was also restored.")
        if request.headers.get('HX-Request'):
            suppliers = Supplier.all_objects.filter(business=business, status='inactive').order_by('-id')
            return render(request, 'Supplier/partials/_archived_suppliers_modal.html', {
                'suppliers': suppliers,
                'business': business,
                'reload_on_close': True,
            })
    return redirect('archived-suppliers', business_slug=business.slug)
