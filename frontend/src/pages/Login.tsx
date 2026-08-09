import Card from "../components/common/Card";
import Button from "../components/common/Button";
import Input from "../components/common/Input";

const Login = () => {
  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        display: "grid",
        placeItems: "center",
      }}
    >
      <Card>
        <h2>DocSentinel</h2>

        <Input label="Email" />

        <Input label="Password" type="password" />

        <Button fullWidth>Login</Button>
      </Card>
    </div>
  );
};

export default Login;