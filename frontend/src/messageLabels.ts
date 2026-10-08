/** Resolve compatibility roles at render time; never change the message itself. */
export function messageRoleLabel(
  role: string,
  personaDisplayName?: string | null,
  userDisplayName?: string | null,
): string {
  switch (role) {
    case "diana": return personaDisplayName?.trim() || "Persona";
    case "user": return userDisplayName?.trim() || "User";
    case "system": return "System";
    case "tool": return "Tool";
    default: return "Message";
  }
}
