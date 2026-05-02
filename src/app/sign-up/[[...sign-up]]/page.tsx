import {SignUp} from "@clerk/nextjs";

export default function SignUnPage() {
    return (
        <div className={"flex min-h-screen items-center justify-center bd-background"}>
            <SignUp
            appearance={{
                elements: {
                    rootBox: "mx-auto",
                    card: "shadow-lg",
            }
            }}
            />
        </div>
    )
}