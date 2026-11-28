import { Link } from "react-router-dom";
import { Empty } from "../components/ui";

export function NotFoundPage() {
  return (
    <Empty title="Page not found">
      <Link to="/">Back to the overview</Link>
    </Empty>
  );
}
