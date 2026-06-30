from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from datetime import timedelta

from core.utils.owner import get_business_for_user
from .models import ActivityEvent

# Create your views here.

@login_required(login_url='login')
def click_event(request, business_slug, event_id):
    business = get_business_for_user(request.user, business_slug)
    event = get_object_or_404(ActivityEvent, business=business, id=event_id)

    # Mark as read on first click
    if not event.is_read:
        event.is_read = True
        event.save(update_fields=['is_read'])

    # Resolve target and redirect
    target = event.target_url(business.slug)
    if target:
        return redirect(target)

    # Fallback if no target URL exists
    return redirect('view-all-activity', business_slug=business_slug)

@login_required(login_url='login')
def view_all_activity(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    events = ActivityEvent.objects.filter(business=business)

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    category = request.GET.get('category')
    if category:
        events = events.filter(verb__startswith=f'{category}.')

    important_only = request.GET.get('important')
    if important_only:
        events = events.filter(is_important=True)

    unread_only = request.GET.get('unread')
    if unread_only:
        events = events.filter(is_read=False)

    paginator = Paginator(events, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    # Precompute target URLs for this page
    events_list = list(page_obj.object_list)
    for e in events_list:
        e.local_date = timezone.localtime(e.created_at).date()
        e._cached_target = e.target_url(business.slug)
        if e._cached_target:
            e.computed_url = reverse('activity-click', kwargs={
                'business_slug': business.slug, 'event_id': e.id
            })
        else:
            e.computed_url = None


    page_obj.object_list = events_list

    return render(request, 'activity/view_all_activity.html', {
        'page_obj': page_obj,
        'today': today,
        'yesterday': yesterday,
        'active_category': category,
        'active_important': important_only,
        'active_unread': unread_only,
        'section': 'activity',
        
    })

@login_required(login_url='login')
@require_POST
def mark_all_read(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    ActivityEvent.objects.filter(
        business=business, is_important=True, is_read=False
    ).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required(login_url='login')
@require_POST
def mark_one_read(request, business_slug, event_id):
    business = get_business_for_user(request.user, business_slug)
    ActivityEvent.objects.filter(
        business=business, pk=event_id
    ).update(is_read=True)
    return JsonResponse({'ok': True})

@login_required(login_url='login')
def notification_poll(request, business_slug):
    # The notification_badge + business_context processors fill
    # notification_count / notification_events / current_business automatically.
    return render(request, 'partials/_topbar_notif.html')
