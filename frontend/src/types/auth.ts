/** Sign-in shapes. Mirrors backend/sigma_editor/api/auth.py and auth/users.py. */

export interface PublicUser {
  username: string;
  display_name: string;
  enabled: boolean;
  must_change_password: boolean;
}

export interface AuthStatus {
  auth_required: boolean;
  accounts: number;
  signed_in: boolean;
  user: PublicUser | null;
  password_rule: string;
}

export interface LoginBody {
  username: string;
  password: string;
  remember: boolean;
}

export interface ChangePasswordBody {
  current_password: string;
  new_password: string;
}

export const MIN_PASSWORD_LENGTH = 8;
