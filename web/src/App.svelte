<script lang="ts">
  import AuthGate from './components/AuthGate.svelte';
  import AppShell from './components/AppShell.svelte';
</script>

<!--
  AuthGate owns the whole authentication lifecycle and only instantiates its
  children snippet once signed in. Keeping the live-sync app in a separate
  AppShell (rather than inline here) is load-bearing: AppShell's onMount — which
  opens the SSE stream and refreshes the list — must not run until AuthGate has
  confirmed a session. Inlining it would start live-sync for an UNAUTHENTICATED
  visitor sitting on the sign-in gate, whose /api/events 401s into a reload loop.
-->
<AuthGate>
  <AppShell />
</AuthGate>
