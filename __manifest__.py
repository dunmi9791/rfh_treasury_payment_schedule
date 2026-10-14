{
    'name': 'RFH Treasury Payment Schedule',
    'version': '18.0.1.1.0',
    'category': 'Accounting/Finance',
    'summary': 'Treasury payment queue, scheduling, batching, and originating-document hooks',
    'description': """
        Centralises all payable obligations from purchase orders, vendor bills,
        expense sheets, and custom documents into a single Treasury queue.
        Treasury staff can prioritise, schedule, batch, approve and mark payments
        as paid, with automatic callbacks to the originating document.

        Purchase orders need a Procurement Director approval before Treasury
        can queue, approve, or pay the order or a vendor bill linked to it.
        Register Payment and the other pay actions on that bill are blocked
        the same way. Bills with no purchase order are unchanged. Confirmation
        itself is not blocked. Orders already confirmed when this version is
        installed or upgraded are grandfathered so live payments continue;
        draft orders are not.
    """,
    'author': 'Riders Workshop',
    'depends': [
        'base',
        'mail',
        'account',
        'purchase',
        'hr_expense',
    ],
    'data': [
        # Security (load first)
        'security/security.xml',
        'security/ir.model.access.csv',
        # Sequences
        'data/sequence.xml',
        'data/po_approval_data.xml',
        # Views
        'views/treasury_payment_schedule_views.xml',
        'views/treasury_payment_batch_views.xml',
        'views/wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/po_approval_wizard_views.xml',
        'views/account_move_views.xml',
        'views/res_config_settings_views.xml',
        # Menus (load last, after all actions exist)
        'views/treasury_menu.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
