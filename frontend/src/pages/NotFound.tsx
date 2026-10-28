import { Link } from "react-router-dom";

const NotFound = () => (
  <main className="notFoundPage" aria-labelledby="not-found-title">
    <p className="notFoundCode">404</p>
    <h1 id="not-found-title">Page not found</h1>
    <p>The address may be incorrect, or the page may have moved.</p>
    <Link className="notFoundLink" to="/">Return to DocSentinel</Link>
  </main>
);

export default NotFound;
