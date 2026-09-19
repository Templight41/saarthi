import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../../services/api'
import { useRefreshAll, useTransactions } from '../../hooks/queries'
import { Button, Chip, MicroLabel, Mono } from '../ui'

const PAYMENT_STATUSES = ['SUCCESS', 'PAYMENT_PENDING', 'FAILED']
const SETTLEMENT_STATUSES = ['PENDING', 'OVERDUE', 'COMPLETED', 'FAILED', 'none']

function tone(status: string) {
  if (status === 'SUCCESS' || status === 'COMPLETED') return 'verified' as const
  if (status === 'FAILED') return 'danger' as const
  return 'acting' as const
}

/**
 * Hand-made fixtures, for testing.
 *
 * Writing here is a simulation control, not an agent capability: Saarthi has
 * no tool that creates a transaction, and giving it one would put the ledger
 * under the model's control. Resetting the fixtures or running any scenario
 * wipes whatever is made here, which is what keeps the demo repeatable.
 */
export function TransactionLab({ merchantId = 'M1001' }: { merchantId?: string }) {
  const { data: transactions } = useTransactions()
  const refresh = useRefreshAll()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const [amount, setAmount] = useState('2500.00')
  const [paymentStatus, setPaymentStatus] = useState('PAYMENT_PENDING')
  const [settlementStatus, setSettlementStatus] = useState('PENDING')
  const [description, setDescription] = useState('Manual test order')
  const [transactionId, setTransactionId] = useState('')
  const [announce, setAnnounce] = useState(false)

  async function create() {
    setBusy(true)
    setNote(null)
    try {
      const created = await api.createTransaction({
        merchant_id: merchantId,
        amount,
        payment_status: paymentStatus,
        settlement_status: settlementStatus === 'none' ? null : settlementStatus,
        description,
        transaction_id: transactionId.trim() || null,
        customer_debited: paymentStatus !== 'FAILED',
        announce_on_soundbox: announce,
      })
      setNote(`Created ${created.transaction_id}`)
      setTransactionId('')
      refresh()
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not create it')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setBusy(true)
    try {
      await api.deleteTransaction(id)
      setNote(`Deleted ${id}`)
      refresh()
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not delete it')
    } finally {
      setBusy(false)
    }
  }

  const field =
    'w-full rounded-sm border border-line bg-ground px-2 py-1.5 text-[12px] text-ink outline-none focus:border-line-bright'

  return (
    <div>
      <div className="flex items-center justify-between">
        <MicroLabel>Transactions ({transactions?.length ?? 0})</MicroLabel>
        <Button tone="ghost" onClick={() => setOpen(!open)}>
          <span className="flex items-center gap-1.5">
            <Plus size={12} /> {open ? 'Close' : 'Add one'}
          </span>
        </Button>
      </div>

      {open && (
        <div className="mb-3 space-y-2 rounded-sm border border-line bg-panel-raised p-2.5">
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">Amount</span>
              <input className={field} value={amount} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="block">
              <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
                Transaction id
              </span>
              <input
                className={field}
                placeholder="auto"
                value={transactionId}
                onChange={(e) => setTransactionId(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
                Payment
              </span>
              <select
                className={field}
                value={paymentStatus}
                onChange={(e) => setPaymentStatus(e.target.value)}
              >
                {PAYMENT_STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
                Settlement
              </span>
              <select
                className={field}
                value={settlementStatus}
                onChange={(e) => setSettlementStatus(e.target.value)}
              >
                {SETTLEMENT_STATUSES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block">
            <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
              Description
            </span>
            <input
              className={field}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          <label className="flex items-center gap-2 text-[11px] text-ink-dim">
            <input
              type="checkbox"
              checked={announce}
              onChange={(e) => setAnnounce(e.target.checked)}
            />
            Also announce it on the Soundbox
          </label>

          <Button tone="primary" onClick={create} disabled={busy}>
            Create transaction
          </Button>
          <p className="text-[10px] leading-snug text-ink-faint">
            Reset, or running any scenario, wipes anything made here.
          </p>
        </div>
      )}

      <div className="max-h-56 space-y-1.5 overflow-y-auto pr-1">
        {transactions?.map((t) => (
          <div
            key={t.id}
            className="flex items-center gap-2 rounded-sm border border-line bg-panel-raised px-2.5 py-1.5"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Mono className="truncate text-[11px] text-ink">{t.id}</Mono>
                <Mono className="text-[11px] text-ink-dim">₹{t.amount}</Mono>
              </div>
              <div className="mt-0.5 flex items-center gap-1.5">
                <Chip tone={tone(t.payment_status)}>{t.payment_status.toLowerCase()}</Chip>
                {t.settlement && (
                  <Chip tone={tone(t.settlement.status)}>
                    stl {t.settlement.status.toLowerCase()}
                  </Chip>
                )}
              </div>
            </div>
            <button
              type="button"
              onClick={() => remove(t.id)}
              disabled={busy}
              title="Delete (only if no case uses it)"
              aria-label={`Delete ${t.id}`}
              className="text-ink-faint transition-colors hover:text-danger disabled:opacity-40"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>

      {note && <div className="mono mt-2 text-[10px] text-ink-faint">{note}</div>}
    </div>
  )
}
