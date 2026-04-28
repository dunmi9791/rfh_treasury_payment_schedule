from odoo import models, _
from odoo.exceptions import UserError


class TreasuryPaymentOriginMixin(models.AbstractModel):
    """
    Abstract mixin for any document that wants to push payments into the
    Treasury queue.  Concrete models should override
    _prepare_treasury_payment_schedule_vals() and, optionally,
    action_treasury_payment_completed().
    """

    _name = 'rfh.treasury.payment.origin.mixin'
    _description = 'Treasury Payment Origin Mixin'

    # ------------------------------------------------------------------
    # Public API called from the source document button
    # ------------------------------------------------------------------

    def action_create_treasury_payment_schedule(self):
        self.ensure_one()
        vals = self._prepare_treasury_payment_schedule_vals()
        if not vals:
            raise UserError(_('No payment schedule values could be prepared.'))

        source_model = vals.get('source_model') or self._name
        source_res_id = vals.get('source_res_id') or self.id

        existing = self.env['rfh.treasury.payment.schedule'].search([
            ('source_model', '=', source_model),
            ('source_res_id', '=', source_res_id),
            ('state', 'not in', ['paid', 'cancelled']),
        ], limit=1)
        if existing:
            raise UserError(_(
                'An active treasury payment schedule already exists for this '
                'document: %s. Cancel or complete it before creating a new one.',
                existing.name
            ))

        schedule = self.env['rfh.treasury.payment.schedule'].create(vals)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rfh.treasury.payment.schedule',
            'res_id': schedule.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _prepare_treasury_payment_schedule_vals(self):
        """
        Override in concrete models to return a dict of field values for
        rfh.treasury.payment.schedule.  Must include at least:
            source_model, source_res_id, partner_id, amount, currency_id
        """
        raise NotImplementedError(
            '_prepare_treasury_payment_schedule_vals must be implemented '
            'by the inheriting model.'
        )

    def action_treasury_payment_completed(self, payment_schedule):
        """
        Called by the treasury schedule when the payment is marked paid.
        Override to update the originating document (e.g. set payment state).
        The default implementation only posts a chatter message.
        """
        if hasattr(self, 'message_post'):
            self.message_post(
                body=_('Treasury payment %s has been marked as paid.',
                       payment_schedule.name)
            )
