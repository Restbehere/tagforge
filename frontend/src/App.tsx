import { Route, Switch } from "wouter";

import { Layout } from "@/components/Layout";
import { Dashboard } from "@/pages/Dashboard";
import { Ingest } from "@/pages/Ingest";
import { Scenes } from "@/pages/Scenes";
import { Tags } from "@/pages/Tags";
import { Trends } from "@/pages/Trends";
import { Builder } from "@/pages/Builder";
import { Decompose } from "@/pages/Decompose";
import { Rig } from "@/pages/Rig";
import { Export } from "@/pages/Export";
import { Settings } from "@/pages/Settings";

export default function App() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/ingest" component={Ingest} />
        <Route path="/scenes" component={Scenes} />
        <Route path="/tags" component={Tags} />
        <Route path="/trends" component={Trends} />
        <Route path="/builder" component={Builder} />
        <Route path="/decompose" component={Decompose} />
        <Route path="/rig" component={Rig} />
        <Route path="/export" component={Export} />
        <Route path="/settings" component={Settings} />
        <Route>
          <div className="grid h-full place-items-center text-text-muted">
            Page not found
          </div>
        </Route>
      </Switch>
    </Layout>
  );
}
