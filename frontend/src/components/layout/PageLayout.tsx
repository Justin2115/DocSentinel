import type { ReactNode } from "react";
import SideBar from "./SideBar";
import TopBar from "./TopBar";

interface PageLayoutProps {
  children: ReactNode;
}

const PageLayout = ({ children }: PageLayoutProps) => {
  return (
    <div className="appLayout">
      <a className="skipLink" href="#main-content">Skip to main content</a>
      <SideBar />

      <div className="content">
        <TopBar />

        <main id="main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
};

export default PageLayout;
