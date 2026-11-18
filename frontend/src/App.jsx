import { useState } from 'react';
import TopNav from './components/TopNav.jsx';
import CitizenDiscovery from './pages/CitizenDiscovery.jsx';
import AdminAnalytics from './pages/AdminAnalytics.jsx';

export default function App() {
  const [mode, setMode] = useState('citizen');

  return (
    <div className="min-h-screen">
      <TopNav mode={mode} onChange={setMode} />
      <main className="mx-auto max-w-7xl px-4 py-6">
        {mode === 'citizen' ? <CitizenDiscovery /> : <AdminAnalytics />}
      </main>
    </div>
  );
}
