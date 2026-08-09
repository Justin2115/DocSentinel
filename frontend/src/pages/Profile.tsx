import { useState } from "react";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import EmailOutlinedIcon from "@mui/icons-material/EmailOutlined";
import PhoneOutlinedIcon from "@mui/icons-material/PhoneOutlined";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";

export default function Profile() {
  const saved = localStorage.getItem("profile");

  const initialProfile = saved
    ? JSON.parse(saved)
    : {
        name: "Elvina Binoy",
        email: "elvina@docsentinel.io",
        phone: "",
        role: "Admin",
      };

  const [profile, setProfile] = useState(initialProfile);
  const [editing, setEditing] = useState(false);
  const [savedMessage, setSavedMessage] = useState(false);

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
          <div className="largeAvatar">{initials}</div>

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