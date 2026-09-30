
import StatsGrid from "../components/dashboard/StatsGrid";
import RecentDocuments from "../components/dashboard/RecentDocuments";
import { useAuth } from "../context/AuthContext";

export default function Dashboard() {
  const { user } = useAuth();
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const firstName = user?.name?.trim().split(/\s+/)[0] || "there";

  return (
   
      <div className="dashboard">

        <h1>{greeting}, {firstName}</h1>

        <p>
          Here's what's happening with your documents today.
        </p>

        <StatsGrid />

        <RecentDocuments />

      </div>

    
  );
}