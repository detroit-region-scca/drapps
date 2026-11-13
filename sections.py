"""Each part of the app: its color and icon, from its tile on the home page. The header uses them too, so every
page shows which part of the app it is in. Keyed by the name the page gives in its app_name block."""
from markupsafe import Markup

SECTIONS = {
    'Events': {'color': '#4c8dff', 'icon': Markup('<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4M7.5 13.5h3M7.5 17h6"/>')},
    'Money': {'color': '#3ecf8e', 'icon': Markup('<path d="M6 2.5h9l4 4v15H6z"/><path d="M15 2.5v4h4"/><path d="M12.5 10v8M14.5 11.5h-3a1.25 1.25 0 0 0 0 2.5h2a1.25 1.25 0 0 1 0 2.5h-3"/>')},
    'Annual Plan': {'color': '#4fd1c5', 'icon': Markup('<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/><path d="M15 8h4v4"/>')},
    'Invoices': {'color': '#6ea8ff', 'icon': Markup('<path d="M6 2.5h12v19l-3-2-3 2-3-2-3 2z"/><path d="M9 7.5h6M9 11h6M9 14.5h4"/>')},
    'Members': {'color': '#f5b83d', 'icon': Markup('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.6 3-5.6 6-5.6s5.4 2 6 5.6"/><circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.6c2.6-.4 4.8 1.4 5.5 4.6"/>')},
    'Records': {'color': '#d2232a', 'icon': Markup('<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4"/>')},
    'Operations': {'color': '#ff9f6b', 'icon': Markup('<path d="M2 16V8h11v8M13 11h4l3 3v2h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>')},
    'Board': {'color': '#1d3fb8', 'icon': Markup('<path d="M6 2.5h9l4 4v15H6z"/><path d="M15 2.5v4h4"/><path d="M9 11h7M9 14.5h7M9 18h4"/>')},
    'Treasurer': {'color': '#9aa3ad', 'icon': Markup('<path d="M3 10h18L12 4z"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20.5h18"/>')},
    'Email groups': {'color': '#8b7dd8', 'icon': Markup('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5 12 13l8.5-6.5"/>')},
    'Hassles': {'color': '#e8590c', 'icon': Markup('<path d="M4 4.5h16a1.5 1.5 0 0 1 1.5 1.5v10a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 3.5v-3.5H4A1.5 1.5 0 0 1 2.5 16V6A1.5 1.5 0 0 1 4 4.5z"/><path d="M12 8v4.5M12 15h.01"/>')},
    'App access': {'color': '#17a2b8', 'icon': Markup('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.6 3-5.6 6-5.6s5.4 2 6 5.6"/><path d="M17 8v6M14 11h6"/>')},
    'Admin': {'color': '#6c757d', 'icon': Markup('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>')},
}
SECTIONS['Audit log'] = SECTIONS['Admin']
