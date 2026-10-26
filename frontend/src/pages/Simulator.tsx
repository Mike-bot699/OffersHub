import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { NeedStore } from '../NeedStore'
import { useApp, type StoreRow } from '../state'
import { ErrorBox, Loading, PageHead, Tabs } from '../ui'
import { ThresholdSimulator, type Basket } from './Basket'
import { Simulations, type Summary } from './Analytics'

export default function Simulator() {
  return <NeedStore title="Offer simulator">{(s) => <SimulatorFor store={s} />}</NeedStore>
}

function SimulatorFor({ store }: { store: StoreRow }) {
  const { days } = useApp()
  const [tab, setTab] = useState('threshold')
  const b = useQuery({ queryKey: ['basket', store.id, days], queryFn: () => api.get<Basket>(`/stores/${store.id}/basket?days=${days}`) })
  const s = useQuery({ queryKey: ['summary', store.id, days], queryFn: () => api.get<Summary>(`/stores/${store.id}/summary?days=${days}`) })
  return (
    <>
      <PageHead title="Offer simulator" desc="Compare offer mechanics before launching them: what each would cost, what it could add, and the response needed to break even. Scenarios, not forecasts." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: 'threshold', label: 'Spend threshold & free gift' }, { id: 'other', label: 'Discount depth, multi-buy, bundles, price' },
      ]} />
      {b.isLoading || s.isLoading ? <Loading /> : b.error || s.error ? <ErrorBox error={b.error ?? s.error} />
        : tab === 'threshold' ? <ThresholdSimulator store={store} basket={b.data!} /> : <Simulations store={store} s={s.data!} />}
    </>
  )
}
