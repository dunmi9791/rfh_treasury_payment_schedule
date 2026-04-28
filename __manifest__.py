{
    'name': 'RFH Treasury Payment Schedule',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Finance',
    'summary': 'Treasury payment queue, scheduling, batching, and originating-document hooks',
    'description': """
        Centralises all payable obligations from purchase orders, vendor bills,
        expense sheets, and custom documents into a single Treasury queue.
        Treasury staff can prioritise, schedule, batch, approve and mark payments
        as paid, with automatic callbacks to the originating document.
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
        # Views
        'views/treasury_payment_schedule_views.xml',
        'views/treasury_payment_batch_views.xml',
        'views/wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/account_move_views.xml',
        # Menus (load last, after all actions exist)
        'views/treasury_menu.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
