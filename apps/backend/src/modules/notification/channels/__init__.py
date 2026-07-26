"""
Concrete NotificationChannel adapter implementations - email today, other
channels (SMS, Slack) later. Each implementation satisfies the
NotificationChannel protocol from ../dependencies.py; which one is used
is a configuration choice at application start-up, not a code change.
"""
