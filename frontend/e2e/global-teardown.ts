import { seed } from './seed'

export default function globalTeardown() {
  seed('cleanup')
}
