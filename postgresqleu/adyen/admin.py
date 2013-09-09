from django.contrib import admin

from models import RawNotification, Notification
from models import Report, TransactionStatus, AdyenLog

class RawNotificationAdmin(admin.ModelAdmin):
	list_display = ('dat', 'confirmed',)

class NotificationAdmin(admin.ModelAdmin):
	list_display = ('receivedat', 'eventDate', 'eventCode', 'live', 'success', 'pspReference', )

class ReportAdmin(admin.ModelAdmin):
	list_display = ('receivedat', 'processedat', 'url',)

class TransactionStatusAdmin(admin.ModelAdmin):
	list_display = ('pspReference', 'authorizedat', 'capturedat', )

class AdyenLogAdmin(admin.ModelAdmin):
	list_display = ('timestamp', 'error', 'sent', 'message', )

admin.site.register(RawNotification, RawNotificationAdmin)
admin.site.register(Notification, NotificationAdmin)
admin.site.register(Report, ReportAdmin)
admin.site.register(TransactionStatus, TransactionStatusAdmin)
admin.site.register(AdyenLog, AdyenLogAdmin)