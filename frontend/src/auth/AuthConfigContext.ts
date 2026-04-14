// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { createContext, useContext } from 'react'
import type { AuthConfig } from './msalConfig'

export const AuthConfigContext = createContext<AuthConfig>({clientId: '', tenantId: '', allowedGroupIds: ''})
export const useAuthConfig = () => useContext(AuthConfigContext)
