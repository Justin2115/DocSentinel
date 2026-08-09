
import StatsGrid from "../components/dashboard/StatsGrid";
import RecentDocuments from "../components/dashboard/RecentDocuments";

export default function Dashboard() {
  return (
   
      <div className="dashboard">

        <h1>Good morning, Elvina</h1>

        <p>
          Here's what's happening with your documents today.
        </p>

        <StatsGrid />

        <RecentDocuments />

      </div>

    
  );
}