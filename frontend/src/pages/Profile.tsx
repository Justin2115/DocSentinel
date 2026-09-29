import { useEffect, useState } from "react";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import EmailOutlinedIcon from "@mui/icons-material/EmailOutlined";
import PhoneOutlinedIcon from "@mui/icons-material/PhoneOutlined";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import { useAuth } from "../context/AuthContext";

export default function Profile() {
  const { user } = useAuth();
  const saved = localStorage.getItem("profile");

  const initialProfile = {
    name: user?.name || (saved ? JSON.parse(saved).name : "User"),
    email: user?.email || (saved ? JSON.parse(saved).email : ""),
    phone: saved ? JSON.parse(saved).phone || "" : "",
    role: user?.role
      ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
      : saved
      ? JSON.parse(saved).role || "User"
      : "User",
  };

  const [profile, setProfile] = useState(initialProfile);
  const [editing, setEditing] = useState(false);
  const [savedMessage, setSavedMessage] = useState(false);

  useEffect(() => {
    if (user) {
      setProfile((prev) => ({
        ...prev,
        name: user.name,
        email: user.email,
        role: user.role.charAt(0).toUpperCase() + user.role.slice(1),
      }));
    }
  }, [user]);

  const handleSave = () => {
    localStorage.setItem("profile", JSON.stringify(profile));
    window.dispatchEvent(new Event("profileUpdated"));
    setEditing(false);
    setSavedMessage(true);

    setTimeout(() => {
      setSavedMessage(false);
    }, 2500);
  };

  const initials = profile.name
    .split(" ")
    .map((word: string) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="profilePage">
      <div className="pageHeader">
        <div>
          <h1>Profile</h1>
          <p>Manage your personal information and account details.</p>
        </div>

        <button
          className="saveButton"
          onClick={() => {
            if (editing) {
              handleSave();
            } else {
              setEditing(true);
            }
          }}
        >
          {editing ? "Save changes" : "Edit profile"}
        </button>
      </div>

      {savedMessage && (
        <div className="successMessage">
          Profile updated successfully.
        </div>
      )}

      <div className="profileGrid">
        <section className="profileCard profileOverview">
          {user?.profile_picture ? (
            <img
              src={user.profile_picture}
              alt={profile.name}
              style={{
                width: 80,
                height: 80,
                borderRadius: "50%",
                objectFit: "cover",
                margin: "0 auto 16px",
              }}
            />
          ) : (
            <div className="largeAvatar">{initials}</div>
          )}

          <h2>{profile.name}</h2>
          <p>{profile.email}</p>

          <span className="roleBadge">
            {profile.role}
          </span>
        </section>

        <section className="profileCard">
          <h2>Personal information</h2>

          <div className="profileFields">
            <div className="profileField">
              <label>
                <AccountCircleIcon />
                Full name
              </label>

              {editing ? (
                <input
                  value={profile.name}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      name: e.target.value,
                    })
                  }
                />
              ) : (
                <span>{profile.name}</span>
              )}
            </div>

            <div className="profileField">
              <label>
                <EmailOutlinedIcon />
                Email address
              </label>

              {editing ? (
                <input
                  type="email"
                  value={profile.email}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      email: e.target.value,
                    })
                  }
                />
              ) : (
                <span>{profile.email}</span>
              )}
            </div>

            <div className="profileField">
              <label>
                <PhoneOutlinedIcon />
                Phone number
              </label>

              {editing ? (
                <input
                  value={profile.phone}
                  placeholder="Enter phone number"
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      phone: e.target.value,
                    })
                  }
                />
              ) : (
                <span>
                  {profile.phone || "Not provided"}
                </span>
              )}
            </div>

            <div className="profileField">
              <label>
                <ShieldOutlinedIcon />
                Role
              </label>

              <span>{profile.role}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}