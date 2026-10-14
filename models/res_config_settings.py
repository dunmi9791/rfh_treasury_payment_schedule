from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rfh_po_approver_id = fields.Many2one(
        related='company_id.rfh_po_approver_id',
        readonly=False,
        string='Procurement Director',
    )
