import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AppShell } from "@/components/layout/app-shell"
import { Skeleton } from "@/components/ui/skeleton"
import { ThemeProvider } from "@/components/theme-provider"

const CompanyPage = lazy(() => import("@/pages/company-page").then((module) => ({ default: module.CompanyPage })))
const ComparePage = lazy(() => import("@/pages/compare-page").then((module) => ({ default: module.ComparePage })))
const MacroPage = lazy(() => import("@/pages/macro-page").then((module) => ({ default: module.MacroPage })))
const SearchPage = lazy(() => import("@/pages/search-page").then((module) => ({ default: module.SearchPage })))
const SettingsPage = lazy(() => import("@/pages/settings-page").then((module) => ({ default: module.SettingsPage })))
const SetupPage = lazy(() => import("@/pages/setup-page").then((module) => ({ default: module.SetupPage })))

export default function App() {
  return <ThemeProvider><Suspense fallback={<div className="p-8"><Skeleton className="h-96 w-full"/></div>}><Routes><Route path="setup" element={<SetupPage/>}/><Route element={<AppShell/>}>
    <Route index element={<Navigate to="/search" replace/>}/>
    <Route path="company/:ticker" element={<CompanyPage/>}/>
    <Route path="compare" element={<ComparePage/>}/>
    <Route path="macro" element={<MacroPage/>}/>
    <Route path="search" element={<SearchPage/>}/>
    <Route path="settings" element={<SettingsPage/>}/>
    <Route path="*" element={<Navigate to="/search" replace/>}/>
  </Route></Routes></Suspense></ThemeProvider>
}
