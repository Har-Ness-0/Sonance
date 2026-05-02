"use client"
import {Button} from "@/components/ui/button";
import {toast} from "sonner";

export default function Home() {
  return (
   <Button variant={"outline"} size={"lg"} onClick={()=>toast.success("Hello World")}>
     Click me
   </Button>
  );
}
