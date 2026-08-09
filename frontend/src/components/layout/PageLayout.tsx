import type { ReactNode } from "react";
import SideBar from "./SideBar";
import TopBar from "./TopBar";

interface PageLayoutProps {
  children: ReactNode;
}

const PageLayout = ({ children }: PageLayoutProps) => {
  return (
    <div className="appLayout">
      <SideBar />

      <div className="content">
        <TopBar />

        <main>{children}</main>
      </div>
    </div>
  );
};

export default PageLayout;