/**
 * Remotion entrypoint — registers the Root composition tree.
 * `remotion studio` and `remotion lambda render` both look for this file.
 */
import { registerRoot } from 'remotion';
import { Root } from './Root';

registerRoot(Root);
